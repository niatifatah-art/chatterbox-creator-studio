from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from studio.cancellation import GenerationCancelled, generation_cancel_requested
from studio.ready_voices import get_ready_voice
from studio.runtime_manager import RuntimeManager


VOICE_ID_RE = re.compile(r"^[ab][fm]_[a-z0-9_]+$")
ProgressCallback = Callable[[str, int | None, int | None], None]


@dataclass(frozen=True, slots=True)
class KokoroExecutionResult:
    audio_path: Path
    metadata_path: Path
    model_id: str
    seed: int
    chunk_count: int


class KokoroExecutionAdapter:
    """Execute Kokoro inside its app-owned isolated Python runtime.

    The host Speech Core never imports Kokoro, Torch or Transformers for this route.
    Only a local model snapshot and local ready-voice tensor are accepted. The child
    process has Hub/Transformers offline mode forced on, preventing synthesis from
    turning into an implicit model/voice download.
    """

    def __init__(
        self,
        runtime_manager: RuntimeManager,
        *,
        worker_path: str | Path | None = None,
        timeout_seconds: float = 600.0,
    ):
        self.runtime_manager = runtime_manager
        self.worker_path = (
            Path(worker_path).expanduser().resolve()
            if worker_path is not None
            else Path(__file__).with_name("kokoro_worker.py").resolve()
        )
        self.timeout_seconds = float(timeout_seconds)

    @staticmethod
    def _voice_path(snapshot: Path, voice_id: str) -> Path:
        """Return one allowlisted ready-voice entry without resolving Hub symlinks.

        Hugging Face snapshots intentionally store files as symlinks into the shared
        ``blobs`` directory. Calling ``resolve()`` on the voice file therefore leaves
        the snapshot tree even though the logical snapshot entry is valid. The fixed
        ``voices/<validated-id>.pt`` construction is traversal-safe without following
        the symlink target for containment checks.
        """

        if not VOICE_ID_RE.fullmatch(voice_id):
            raise ValueError("Invalid Kokoro ready voice id.")
        voices_root = snapshot / "voices"
        path = voices_root / f"{voice_id}.pt"
        if not path.is_file():
            raise FileNotFoundError(f"Kokoro ready voice '{voice_id}' is not installed.")
        return path

    def synthesize(
        self,
        *,
        text: str,
        model_snapshot: str | Path,
        model_id: str,
        voice_id: str,
        language: str,
        output_dir: str | Path,
        speed: float = 1.0,
        device: str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> KokoroExecutionResult:
        if model_id != "kokoro-v1.0":
            raise ValueError(f"Unsupported Kokoro model id '{model_id}'.")
        if (language or "en").lower().split("-", 1)[0] != "en":
            raise ValueError("This Kokoro route is currently certified for English only.")
        get_ready_voice("kokoro", voice_id)

        snapshot = Path(model_snapshot).expanduser().resolve()
        if not snapshot.is_dir():
            raise FileNotFoundError("Kokoro model snapshot is missing.")
        self._voice_path(snapshot, voice_id)
        for required in (snapshot / "config.json", snapshot / "kokoro-v1_0.pth"):
            if not required.is_file():
                raise FileNotFoundError(f"Required Kokoro model asset is missing: {required.name}")

        runtime = self.runtime_manager.status("kokoro")
        if not runtime.ready or not runtime.python_path:
            raise RuntimeError(runtime.warning or "Kokoro runtime is not ready.")
        if not self.worker_path.is_file():
            raise RuntimeError("Kokoro worker is missing from this application build.")

        output_root = Path(output_dir).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        text_path = output_root / "input.txt"
        output_path = output_root / "speech.wav"
        metadata_path = output_root / "generation.json"
        text_path.write_text(text, encoding="utf-8")

        resolved_device = (device or "cpu").lower()
        if resolved_device not in {"cpu", "cuda", "mps"}:
            resolved_device = "cpu"

        command = [
            runtime.python_path,
            str(self.worker_path),
            "--model-dir",
            str(snapshot),
            "--voice-id",
            voice_id,
            "--text-file",
            str(text_path),
            "--output",
            str(output_path),
            "--metadata",
            str(metadata_path),
            "--speed",
            str(float(speed)),
            "--device",
            resolved_device,
        ]
        env = os.environ.copy()
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        env["TOKENIZERS_PARALLELISM"] = "false"

        if progress_callback:
            progress_callback("Starting Kokoro runtime", 0, 1)
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
                raise TimeoutError("Kokoro generation exceeded the local timeout.")
            time.sleep(0.1)

        stdout, stderr = process.communicate()
        if process.returncode != 0:
            tail = (stderr or stdout or "Kokoro worker failed.").strip().splitlines()[-1:]
            reason = tail[0][:300] if tail else "Kokoro worker failed."
            raise RuntimeError(reason)
        if not output_path.is_file() or output_path.stat().st_size <= 44:
            raise RuntimeError("Kokoro worker returned no usable WAV audio.")

        chunk_count = 1
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            chunk_count = max(1, int(metadata.get("chunk_count") or 1))
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            pass
        if progress_callback:
            progress_callback("Kokoro complete", 1, 1)
        return KokoroExecutionResult(
            audio_path=output_path,
            metadata_path=metadata_path,
            model_id=model_id,
            seed=0,
            chunk_count=chunk_count,
        )
