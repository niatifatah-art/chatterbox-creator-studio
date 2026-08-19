from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from studio.engine_manager import EngineManager
from studio.protocol import SpeechSynthesisRequest
from studio.ready_voices import create_ready_voice_profile
from studio.synthesis import SpeechSynthesisService
from studio.voice_profile_store import VoiceProfileStore


SMOKE_TEXT = (
    "This is a real local Qwen speech test. The model, runtime, voice identity, "
    "offline generation path, and final provenance must all work together."
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Real Qwen3-TTS ready-voice smoke test")
    parser.add_argument("--output-dir", default="smoke-output/qwen")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    data_root = output_dir / "data"

    manager = EngineManager(data_root)
    runtime = manager.status("qwen3-ready").runtime
    if runtime is None or not runtime.ready:
        runtime = manager.install_runtime("qwen3-ready")
    if runtime is None or not runtime.ready:
        raise RuntimeError("Qwen3-TTS isolated runtime did not become ready.")

    # Import inside the isolated runtime before downloading multi-GB model assets. This
    # makes dependency/package incompatibilities fail early and with a small blast radius.
    if not runtime.python_path:
        raise RuntimeError("Qwen3-TTS runtime did not expose its Python executable.")
    import subprocess

    probe = subprocess.run(
        [
            runtime.python_path,
            "-c",
            (
                "import torch, torchaudio; "
                "from qwen_tts import Qwen3TTSModel; "
                "print(torch.__version__, torchaudio.__version__, Qwen3TTSModel.__name__)"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if probe.returncode != 0:
        raise RuntimeError(
            "Qwen runtime import probe failed: "
            + (probe.stderr or probe.stdout or "unknown import error").strip()[-1200:]
        )

    model = manager.install_model("qwen3-0.6b-custom")
    verification = manager.verify_model("qwen3-0.6b-custom")
    if not model.installed or model.repairable or not verification.valid or verification.source_trusted is not True:
        raise RuntimeError(f"Qwen model verification failed: {verification.warning or 'unknown error'}")

    speech_dir = data_root / "speech-core"
    profiles = VoiceProfileStore(speech_dir / "voice-profiles")
    if profiles.get("qwen-ryan") is None:
        create_ready_voice_profile(
            profiles,
            "qwen-ryan",
            engine_id="qwen3-ready",
            voice_id="Ryan",
            display_name="Qwen Ryan",
            model_id="qwen3-0.6b-custom",
            model_revision=model.revision,
            metadata={"smoke_fixture": True},
        )

    # Installation/downloads are complete. The actual generation path is deliberately
    # offline so a green smoke proves Speech Core never hides a model/network fetch.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    service = SpeechSynthesisService(speech_dir)
    artifact = service.synthesize(
        SpeechSynthesisRequest(
            text=SMOKE_TEXT,
            voice_profile_id="qwen-ryan",
            language="en",
            engine_override="qwen3-ready",
        )
    )

    if artifact.provenance.engine_id != "qwen3-ready":
        raise RuntimeError("Qwen smoke returned the wrong engine provenance.")
    if artifact.provenance.model_id != "qwen3-0.6b-custom":
        raise RuntimeError("Qwen smoke returned the wrong model provenance.")
    if artifact.provenance.model_revision != model.revision:
        raise RuntimeError("Qwen smoke lost the selected immutable model revision.")
    if artifact.duration_seconds <= 0.5:
        raise RuntimeError("Qwen smoke generated unexpectedly short audio.")

    audio_path = service.artifact_store.resolve(artifact.audio)
    final_wav = output_dir / "qwen-smoke.wav"
    shutil.copy2(audio_path, final_wav)
    (output_dir / "qwen-smoke.json").write_text(
        json.dumps(
            {
                "artifact": artifact.to_dict(),
                "runtime": {
                    "runtime_id": runtime.runtime_id,
                    "install_mode": runtime.install_mode,
                    "source_revision": runtime.source_revision,
                    "import_probe": probe.stdout.strip(),
                },
                "model_verification": {
                    "model_id": verification.model_id,
                    "revision": verification.revision,
                    "source_trusted": verification.source_trusted,
                    "valid": verification.valid,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Qwen smoke passed: {artifact.duration_seconds:.2f}s -> {final_wav}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
