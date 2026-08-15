from __future__ import annotations

import re
import threading
from datetime import datetime
from pathlib import Path

from .pauses import Pause, Speech, find_invalid_pause_markers, parse_script, pause_samples

MAX_CHARS_PER_CHUNK = 280
_CHUNK_GAP_SECONDS = 0.06


def _sentence_chunks(text: str, max_chars: int = MAX_CHARS_PER_CHUNK) -> list[str]:
    """Conservatively split long speech at sentence boundaries."""
    text = " ".join((text or "").split())
    if len(text) <= max_chars:
        return [text] if text else []

    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    if not sentences:
        sentences = [text]

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        pieces = [sentence]
        if len(sentence) > max_chars:
            pieces = []
            words = sentence.split()
            part = ""
            for word in words:
                candidate = f"{part} {word}".strip()
                if part and len(candidate) > max_chars:
                    pieces.append(part)
                    part = word
                else:
                    part = candidate
            if part:
                pieces.append(part)

        for piece in pieces:
            candidate = f"{current} {piece}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


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


class ChatterboxEngine:
    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.device, self.device_label = detect_device()
        self._model = None
        self._voice_key: tuple[str, float] | None = None
        self._lock = threading.RLock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self):
        if self._model is None:
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS

            self._model = ChatterboxMultilingualTTS.from_pretrained(
                device=self.device,
                t3_model="v3",
            )
        return self._model

    def _prepare_voice(self, voice_path: Path, exaggeration: float) -> None:
        model = self.load()
        key = (str(voice_path.resolve()), round(float(exaggeration), 4))
        if key != self._voice_key:
            model.prepare_conditionals(str(voice_path), exaggeration=float(exaggeration))
            self._voice_key = key

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
        language_id: str,
        exaggeration: float,
        cfg_weight: float,
        temperature: float,
        repetition_penalty: float,
        min_p: float,
        top_p: float,
        speech_speed: float,
    ) -> Path:
        import torch
        import torchaudio as ta

        invalid = find_invalid_pause_markers(script)
        if invalid:
            raise ValueError(
                "Invalid pause syntax: " + ", ".join(invalid[:3]) + ". Use [pause=0.35] or [pause=250ms]."
            )
        segments = parse_script(script)
        if not any(isinstance(segment, Speech) for segment in segments):
            raise ValueError("Add some text to synthesize.")

        voice = Path(voice_path)
        if not voice.exists():
            raise FileNotFoundError("The selected voice profile could not be found.")

        with self._lock:
            model = self.load()
            self._prepare_voice(voice, float(exaggeration))
            sample_rate = int(model.sr)
            clips = []

            for segment in segments:
                if isinstance(segment, Pause):
                    clips.append(self._silence(segment.seconds, sample_rate))
                    continue

                chunks = _sentence_chunks(segment.text)
                for index, chunk in enumerate(chunks):
                    wav = model.generate(
                        chunk,
                        language_id=language_id,
                        audio_prompt_path=None,
                        exaggeration=float(exaggeration),
                        cfg_weight=float(cfg_weight),
                        temperature=float(temperature),
                        repetition_penalty=float(repetition_penalty),
                        min_p=float(min_p),
                        top_p=float(top_p),
                    )
                    wav = self._time_stretch(wav, float(speech_speed))
                    clips.append(wav.to(dtype=torch.float32))
                    if index < len(chunks) - 1:
                        clips.append(self._silence(_CHUNK_GAP_SECONDS, sample_rate))

            final = torch.cat(clips, dim=-1)
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")[:-3]
            output = self.output_dir / f"chatterbox_{timestamp}.wav"
            ta.save(str(output), final, sample_rate)
            return output

    def recent_outputs(self, limit: int = 12) -> list[str]:
        files = sorted(self.output_dir.glob("*.wav"), key=lambda path: path.stat().st_mtime, reverse=True)
        return [path.name for path in files[:limit]]

    def output_path(self, filename: str | None) -> Path | None:
        if not filename:
            return None
        safe = Path(filename).name
        path = self.output_dir / safe
        return path if path.exists() else None
