from __future__ import annotations

import json
import random
import secrets
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .models import (
    DEFAULT_MODEL_ID,
    MODEL_SPECS,
    GenerationOptions,
    create_adapter,
)
from .pauses import Pause, Speech, find_invalid_pause_markers, parse_script, pause_samples
from .text import DEFAULT_MAX_CHARS, smart_chunks

DEFAULT_CHUNK_GAP_SECONDS = 0.06
ProgressCallback = Callable[[str, int | None, int | None], None]


@dataclass(frozen=True)
class GenerationResult:
    audio_path: Path
    metadata_path: Path
    model_id: str
    model_name: str
    seed: int
    chunk_count: int


def _sentence_chunks(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """Backward-compatible wrapper for the original v0.1 helper."""
    return smart_chunks(text, max_chars=max_chars)


def detect_device() -> tuple[str, str]:
    import torch

    if torch.cuda.is_available():
        try:
            name = torch.cuda.get_device_name(0)
        except Exception:
            name = "CUDA GPU"
        return "cuda", f"CUDA · {name}"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps", "Apple MPS"
    return "cpu", "CPU"


def _resolve_seed(seed: int | None) -> int:
    if seed is None or int(seed) < 0:
        return secrets.randbelow(2_147_483_647)
    return int(seed)


def _seed_everything(seed: int) -> None:
    """Seed the RNGs used by Chatterbox and its common dependencies."""
    random.seed(seed)

    try:
        import numpy as np

        np.random.seed(seed)
    except Exception:
        pass

    import torch

    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)


class ChatterboxEngine:
    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.device, self.device_label = detect_device()
        self._adapter = None
        self._model_id: str | None = None
        self._model_paths: dict[str, Path] = {}
        self._lock = threading.RLock()

    @property
    def loaded(self) -> bool:
        return bool(self._adapter and self._adapter.loaded)

    @property
    def loaded_model_id(self) -> str | None:
        return self._model_id if self.loaded else None

    def set_device(self, device: str, label: str | None = None) -> None:
        """Switch compute backend safely; active model memory is released first."""
        normalized = str(device or "cpu").lower()
        if normalized not in {"cpu", "cuda", "mps"}:
            raise ValueError(f"Unsupported compute device '{device}'.")
        if normalized != self.device:
            self.unload()
            self.device = normalized
        self.device_label = label or normalized.upper()

    def set_model_path(self, model_id: str, path: str | Path | None) -> None:
        """Pin a model to a local snapshot so generation never silently updates it."""
        if model_id not in MODEL_SPECS:
            raise ValueError(f"Unknown model '{model_id}'.")
        new_path = Path(path).resolve() if path else None
        previous = self._model_paths.get(model_id)
        if self._model_id == model_id and previous != new_path:
            self.unload()
        if new_path is None:
            self._model_paths.pop(model_id, None)
        else:
            if not new_path.exists():
                raise FileNotFoundError(f"Local model snapshot not found: {new_path}")
            self._model_paths[model_id] = new_path

    def _adapter_for(self, model_id: str):
        if model_id not in MODEL_SPECS:
            raise ValueError(f"Unknown model '{model_id}'.")
        if self._adapter is None or self._model_id != model_id:
            self.unload()
            self._adapter = create_adapter(
                model_id,
                self.device,
                model_dir=self._model_paths.get(model_id),
            )
            self._model_id = model_id
        return self._adapter

    def load_model(self, model_id: str, progress_callback: ProgressCallback | None = None) -> None:
        """Load the selected model into memory without generating audio."""
        with self._lock:
            if progress_callback:
                progress_callback("Loading model into memory…", None, None)
            adapter = self._adapter_for(model_id)
            _ = adapter.sample_rate
            if progress_callback:
                progress_callback("Model ready", 1, 1)

    def unload(self) -> None:
        if self._adapter is not None:
            self._adapter.unload()
        self._adapter = None
        self._model_id = None

    @staticmethod
    def _time_stretch(wav, speed: float):
        if abs(float(speed) - 1.0) < 1e-3:
            return wav
        import librosa
        import numpy as np
        import torch

        y = wav.squeeze(0).detach().cpu().numpy().astype(np.float32, copy=False)
        stretched = librosa.effects.time_stretch(y, rate=float(speed))
        return torch.from_numpy(stretched).unsqueeze(0)

    @staticmethod
    def _silence(seconds: float, sample_rate: int):
        import torch

        return torch.zeros((1, pause_samples(seconds, sample_rate)), dtype=torch.float32)

    def generate(
        self,
        script: str,
        voice_path: str | Path,
        model_id: str = DEFAULT_MODEL_ID,
        language_id: str = "en",
        exaggeration: float = 0.5,
        cfg_weight: float = 0.5,
        temperature: float = 0.8,
        repetition_penalty: float = 1.2,
        min_p: float = 0.05,
        top_p: float = 1.0,
        top_k: int = 1000,
        speech_speed: float = 1.0,
        raw_mode: bool = False,
        smart_chunking: bool = True,
        max_chars: int = DEFAULT_MAX_CHARS,
        chunk_gap_seconds: float = DEFAULT_CHUNK_GAP_SECONDS,
        seed: int | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> GenerationResult:
        import torch
        import torchaudio as ta

        script = script or ""
        if not script.strip():
            raise ValueError("Add some text to synthesize.")
        if model_id not in MODEL_SPECS:
            raise ValueError(f"Unknown model '{model_id}'.")
        if not 0.0 <= float(chunk_gap_seconds) <= 3.0:
            raise ValueError("Chunk gap must be between 0 and 3 seconds.")
        if not 0.5 <= float(speech_speed) <= 2.0:
            raise ValueError("Post speech speed must be between 0.5x and 2.0x.")

        voice = Path(voice_path)
        if not voice.exists():
            raise FileNotFoundError("The selected voice profile could not be found.")

        spec = MODEL_SPECS[model_id]
        if language_id not in spec.languages:
            if spec.languages == ("en",):
                language_id = "en"
            else:
                raise ValueError(f"{spec.name} does not support language '{language_id}'.")

        actual_seed = _resolve_seed(seed)
        options = GenerationOptions(
            language_id=language_id,
            exaggeration=float(exaggeration),
            cfg_weight=float(cfg_weight),
            temperature=float(temperature),
            repetition_penalty=float(repetition_penalty),
            min_p=float(min_p),
            top_p=float(top_p),
            top_k=int(top_k),
        )

        if raw_mode:
            segments = [Speech(script.strip())]
        else:
            invalid = find_invalid_pause_markers(script)
            if invalid:
                raise ValueError(
                    "Invalid pause syntax: "
                    + ", ".join(invalid[:3])
                    + ". Use [pause=0.35] or [pause=250ms]."
                )
            segments = parse_script(script)
            if not any(isinstance(segment, Speech) for segment in segments):
                raise ValueError("Add some text to synthesize.")

        # Count only actual synthesis chunks. Digital pauses are instant and should
        # not make the progress indicator look slower than the model really is.
        planned_chunks = 0
        for segment in segments:
            if not isinstance(segment, Speech):
                continue
            if raw_mode or not smart_chunking:
                planned_chunks += 1
            else:
                planned_chunks += len(smart_chunks(segment.text, max_chars=int(max_chars)))

        with self._lock:
            adapter = self._adapter_for(model_id)
            _seed_everything(actual_seed)
            clips = []
            generated_chunks: list[str] = []
            sample_rate: int | None = None
            completed_chunks = 0
            if progress_callback:
                progress_callback("Loading model and preparing voice…", None, None)

            for segment in segments:
                if isinstance(segment, Pause):
                    if sample_rate is None:
                        sample_rate = int(adapter.sample_rate)
                    clips.append(self._silence(segment.seconds, sample_rate))
                    continue

                if raw_mode or not smart_chunking:
                    chunks = [segment.text]
                else:
                    chunks = smart_chunks(segment.text, max_chars=int(max_chars))

                for index, chunk in enumerate(chunks):
                    if progress_callback:
                        progress_callback("Generating speech…", completed_chunks, planned_chunks or None)
                    wav = adapter.generate(chunk, voice, options)
                    completed_chunks += 1
                    if progress_callback:
                        progress_callback("Generating speech…", completed_chunks, planned_chunks or None)
                    if sample_rate is None:
                        sample_rate = int(adapter.sample_rate)
                    wav = self._time_stretch(wav, float(speech_speed))
                    clips.append(wav.to(dtype=torch.float32).cpu())
                    generated_chunks.append(chunk)
                    if (
                        not raw_mode
                        and smart_chunking
                        and index < len(chunks) - 1
                        and float(chunk_gap_seconds) > 0
                    ):
                        clips.append(self._silence(float(chunk_gap_seconds), sample_rate))

            if not clips or sample_rate is None:
                raise RuntimeError("Generation produced no audio clips.")

            if progress_callback:
                progress_callback("Saving audio…", planned_chunks, planned_chunks or None)
            final = torch.cat(clips, dim=-1)
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")[:-3]
            safe_model = model_id.replace("/", "-")
            output = self.output_dir / f"{safe_model}_{timestamp}.wav"
            ta.save(str(output), final, sample_rate)

            metadata = {
                "schema_version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "audio_file": output.name,
                "model": {
                    "id": model_id,
                    "name": spec.name,
                    "local_snapshot": str(self._model_paths.get(model_id)) if self._model_paths.get(model_id) else None,
                },
                "device": self.device_label,
                "voice_file": voice.name,
                "language_id": language_id,
                "seed": actual_seed,
                "mode": "raw" if raw_mode else "studio",
                "smart_chunking": bool(smart_chunking and not raw_mode),
                "max_chars": int(max_chars),
                "chunk_gap_seconds": float(chunk_gap_seconds),
                "speech_speed": float(speech_speed),
                "generation_options": asdict(options),
                "original_script": script,
                "generated_chunks": generated_chunks,
                "chunk_count": len(generated_chunks),
            }
            metadata_path = output.with_suffix(".json")
            metadata_path.write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            return GenerationResult(
                audio_path=output,
                metadata_path=metadata_path,
                model_id=model_id,
                model_name=spec.name,
                seed=actual_seed,
                chunk_count=len(generated_chunks),
            )

    def recent_outputs(self, limit: int = 20) -> list[str]:
        files = sorted(
            self.output_dir.glob("*.wav"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return [path.name for path in files[:limit]]

    def output_path(self, filename: str | None) -> Path | None:
        if not filename:
            return None
        safe = Path(filename).name
        path = self.output_dir / safe
        return path if path.exists() else None

    def metadata_path(self, filename: str | None) -> Path | None:
        path = self.output_path(filename)
        if path is None:
            return None
        metadata = path.with_suffix(".json")
        return metadata if metadata.exists() else None
