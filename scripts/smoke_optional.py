from __future__ import annotations

import argparse
import json
from pathlib import Path

from studio.audio import AudioProcessOptions, process_audio
from studio.engine import ChatterboxEngine
from studio.model_manager import LocalModelManager
from studio.preprocess import PreprocessOptions, process_text
from studio.quality import verify_with_faster_whisper


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test optional Creator Studio helpers")
    parser.add_argument("--voice", required=True)
    parser.add_argument("--output-dir", default="optional-smoke-output")
    args = parser.parse_args()

    voice = Path(args.voice)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    number_preview = process_text(
        "In 2026 I built 2 local tools.",
        language_id="en",
        options=PreprocessOptions(normalize_numbers=True),
    )
    if not number_preview.changed or "2026" in number_preview.processed or " 2 " in f" {number_preview.processed} ":
        raise RuntimeError(f"num2words preprocessing did not run: {number_preview.processed!r}")
    if number_preview.warnings:
        raise RuntimeError("Number preprocessing returned a warning despite optional dependencies being installed")

    # Speech Core deliberately never starts a multi-GB model download from synthesize().
    # The smoke therefore exercises the explicit install/select lifecycle first, then
    # proves the Core-backed compatibility constructor can use that exact snapshot.
    manager = LocalModelManager(output_dir / "model_state.json")
    managed = manager.download("nano", progress=lambda current, total, desc: None)
    if not managed.installed or not managed.snapshot_path or not managed.revision:
        raise RuntimeError("Explicit Nano install did not produce a pinned local snapshot")

    source_text = "This is a local verification test."
    engine = ChatterboxEngine(output_dir)
    engine.set_model_path("nano", managed.snapshot_path)
    result = engine.generate(
        script=source_text,
        voice_path=voice,
        model_id="nano",
        language_id="en",
        raw_mode=True,
        smart_chunking=False,
        seed=424242,
    )

    verification = verify_with_faster_whisper(
        result.audio_path,
        source_text,
        language_id="en",
        model_size="tiny",
        threshold=0.0,
    )
    if not verification.available:
        raise RuntimeError(verification.warning or "Faster-Whisper is unavailable")
    if verification.similarity is None or not verification.transcript.strip():
        raise RuntimeError(f"Faster-Whisper did not return a usable transcript: {verification.warning}")

    processed = process_audio(
        result.audio_path,
        output_dir / "finished.wav",
        AudioProcessOptions(trim_silence=True, peak_normalize=True, fade_ms=15),
    )
    if not processed.exists() or processed.stat().st_size <= 44:
        raise RuntimeError("Optional finishing smoke did not produce a WAV")

    engine.unload()
    print(
        json.dumps(
            {
                "ok": True,
                "nano_revision": managed.revision,
                "number_preview": number_preview.processed,
                "transcript": verification.transcript,
                "similarity": verification.similarity,
                "generated": str(result.audio_path),
                "finished": str(processed),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
