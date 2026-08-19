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
from studio.protocol import SpeechSynthesisRequest, VoiceSourceKind
from studio.synthesis import SpeechSynthesisService
from studio.voice_profile_store import VoiceProfileStore


SMOKE_TEXT = (
    "A reliable local voice tool should be simple for creators and predictable for developers. "
    "This smoke test installs the lightweight Kokoro runtime in its own environment, downloads the "
    "official model and ready voice assets once, then deliberately switches the synthesis process to "
    "offline mode. The Speech Core receives only a voice profile, text, language, and a generic engine "
    "override. It must return a valid logical speech artifact with the exact model revision in provenance, "
    "without a clone reference and without downloading anything while speech is generated. This sample is "
    "long enough to exercise normal English phrasing rather than a tiny one word demo, while remaining "
    "small enough for a practical continuous integration check."
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Real Kokoro ready-voice smoke test")
    parser.add_argument("--output-dir", default="smoke-output/kokoro")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    data_root = output_dir / "data"

    manager = EngineManager(data_root)
    runtime = manager.install_runtime("kokoro") if not manager.status("kokoro").runtime.ready else manager.status("kokoro").runtime
    if runtime is None or not runtime.ready:
        raise RuntimeError("Kokoro isolated runtime did not become ready.")

    model = manager.install_model("kokoro-v1.0")
    verification = manager.verify_model("kokoro-v1.0")
    if not model.installed or model.repairable or not verification.valid or verification.source_trusted is not True:
        raise RuntimeError(f"Kokoro model verification failed: {verification.warning or 'unknown error'}")

    speech_dir = data_root / "speech-core"
    profiles = VoiceProfileStore(speech_dir / "voice-profiles")
    if profiles.get("kokoro-heart") is None:
        profiles.create(
            "kokoro-heart",
            "Kokoro Heart",
            source_kind=VoiceSourceKind.READY,
            source_voice_id="af_heart",
            supported_languages=("en",),
            metadata={"smoke_fixture": True},
        )

    # Installation/downloads are complete. Generation must work without a network path.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    service = SpeechSynthesisService(speech_dir)
    artifact = service.synthesize(
        SpeechSynthesisRequest(
            text=SMOKE_TEXT,
            voice_profile_id="kokoro-heart",
            language="en",
            engine_override="kokoro",
        )
    )

    if artifact.provenance.engine_id != "kokoro" or artifact.provenance.model_id != "kokoro-v1.0":
        raise RuntimeError("Kokoro smoke returned incorrect provenance.")
    if artifact.provenance.model_revision != model.revision:
        raise RuntimeError("Kokoro smoke lost the selected immutable model revision.")
    if artifact.duration_seconds <= 1.0:
        raise RuntimeError("Kokoro smoke generated unexpectedly short audio.")

    audio_path = service.artifact_store.resolve(artifact.audio)
    final_wav = output_dir / "kokoro-smoke.wav"
    shutil.copy2(audio_path, final_wav)
    (output_dir / "kokoro-smoke.json").write_text(
        json.dumps(
            {
                "artifact": artifact.to_dict(),
                "runtime": {
                    "runtime_id": runtime.runtime_id,
                    "install_mode": runtime.install_mode,
                    "source_revision": runtime.source_revision,
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
    print(f"Kokoro smoke passed: {artifact.duration_seconds:.2f}s -> {final_wav}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
