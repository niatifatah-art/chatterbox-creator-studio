from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from studio.cancellation import GenerationCancelled, generation_cancel_requested
from studio.ready_voices import get_ready_voice
from studio.runtime_manager import RuntimeManager


ProgressCallback = Callable[[str, int | None, int | None], None]

MODEL_MODES = {
    "qwen3-0.6b-base": "clone",
    "qwen3-0.6b-custom": "ready",
    "qwen3-1.7b-voice-design": "design",
}


@dataclass(frozen=True, slots=True)
class QwenExecutionResult:
    audio_path: Path
    metadata_path: Path
    model_id: str
    seed: int
    chunk_count: int


class QwenExecutionAdapter:
    """Execute Qwen3-TTS in the app-owned isolated runtime.

    The host process never imports Qwen, Torch, Transformers or model weights. All model
    inputs are local paths and the child is forced offline before heavy imports.
    """

    def __init__(
        self,
        runtime_manager: RuntimeManager,
        *,
        worker_path: str | Path | None = None,
        timeout_seconds: float = 1200.0,
    ):
        self.runtime_manager = runtime_manager
        self.worker_path = (
            Path(worker_path).expanduser().resolve()
            if worker_path is not None
            else Path(__file__).with_name("qwen_worker.py").resolve()
        )
        self.timeout_seconds = float(timeout_seconds)

    @staticmethod
    def _validate_snapshot(snapshot: Path) -> None:
        required = (
            snapshot / "config.json",
            snapshot / "model.safetensors",
            snapshot / "speech_tokenizer" / "config.json",
            snapshot / "speech_tokenizer" / "model.safetensors",
        )
        missing = [path.relative_to(snapshot).as_posix() for path in required if not path.is_file()]
        if missing:
            raise FileNotFoundError(
                "Required Qwen3-TTS model assets are missing: " + ", ".join(missing)
            )

    def synthesize(
        self,
        *,
        text: str,
        model_snapshot: str | Path,
        model_id: str,
        language: str,
        output_dir: str | Path,
        device: str | None = None,
        voice_id: str | None = None,
        reference_audio: str | Path | None = None,
        reference_text: str | None = None,
        instruct: str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> QwenExecutionResult:
        try:
            mode = MODEL_MODES[model_id]
        except KeyError as exc:
            raise ValueError(f"Unsupported Qwen3-TTS model id '{model_id}'.") from exc

        snapshot = Path(model_snapshot).expanduser().resolve()
        if not snapshot.is_dir():
            raise FileNotFoundError("Qwen3-TTS model snapshot is missing.")
        self._validate_snapshot(snapshot)

        resolved_reference: Path | None = None
        if mode == "clone":
            if reference_audio is None:
                raise ValueError("Qwen clone generation requires a local reference audio file.")
            resolved_reference = Path(reference_audio).expanduser().resolve()
            if not resolved_reference.is_file():
                raise FileNotFoundError("Qwen clone reference audio is missing.")
        elif mode == "ready":
            if not voice_id:
                raise ValueError("Qwen CustomVoice requires a ready voice id.")
            get_ready_voice("qwen3-ready", voice_id)
        elif not (instruct or "").strip():
            raise ValueError("Qwen VoiceDesign requires a voice description.")

        runtime = self.runtime_manager.status("qwen3-tts")
        if not runtime.ready or not runtime.python_path:
            raise RuntimeError(runtime.warning or "Qwen3-TTS runtime is not ready.")
        if not self.worker_path.is_file():
            raise RuntimeError("Qwen3-TTS worker is missing from this application build.")

        output_root = Path(output_dir).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        text_path = output_root / "input.txt"
        output_path = output_root / "speech.wav"
        metadata_path = output_root / "generation.json"
        text_path.write_text(text, encoding="utf-8")

        command = [
            runtime.python_path,
            str(self.worker_path),
            "--model-dir",
            str(snapshot),
            "--model-id",
            model_id,
            "--text-file",
            str(text_path),
            "--output",
            str(output_path),
            "--metadata",
            str(metadata_path),
            "--language",
            language,
            "--device",
            (device or "cpu").lower(),
        ]

        if voice_id:
            command.extend(["--voice-id", voice_id])
        if resolved_reference is not None:
            command.extend(["--reference-audio", str(resolved_reference)])
        if reference_text:
            reference_text_path = output_root / "reference.txt"
            reference_text_path.write_text(reference_text, encoding="utf-8")
            command.extend(["--reference-text-file", str(reference_text_path)])
        if instruct:
            instruct_path = output_root / "instruction.txt"
            instruct_path.write_text(instruct, encoding="utf-8")
            command.extend(["--instruct-file", str(instruct_path)])

        env = os.environ.copy()
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        env["TOKENIZERS_PARALLELISM"] = "false"

        if progress_callback:
            progress_callback("Starting Qwen3-TTS runtime", 0, 1)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        started = time.monotonic()
        while process.poll() is None:
            if generation_cancel_requested():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise GenerationCancelled("Generation stopped.")
            if time.monotonic() - started > self.timeout_seconds:
                process.kill()
                raise TimeoutError("Qwen3-TTS generation exceeded the local timeout.")
            time.sleep(0.1)

        stdout, stderr = process.communicate()
        if process.returncode != 0:
            tail = (stderr or stdout or "Qwen3-TTS worker failed.").strip().splitlines()[-1:]
            reason = tail[0][:300] if tail else "Qwen3-TTS worker failed."
            raise RuntimeError(reason)
        if not output_path.is_file() or output_path.stat().st_size <= 44:
            raise RuntimeError("Qwen3-TTS worker returned no usable WAV audio.")

        chunk_count = 1
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            chunk_count = max(1, int(metadata.get("chunk_count") or 1))
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            pass

        if progress_callback:
            progress_callback("Qwen3-TTS complete", 1, 1)
        return QwenExecutionResult(
            audio_path=output_path,
            metadata_path=metadata_path,
            model_id=model_id,
            seed=0,
            chunk_count=chunk_count,
        )
