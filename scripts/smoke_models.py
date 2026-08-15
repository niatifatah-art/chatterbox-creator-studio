from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
import torchaudio

from studio.engine import ChatterboxEngine
from studio.model_manager import LocalModelManager
from studio.models import MODEL_SPECS
from studio.quality import analyze_audio


def main() -> None:
    parser = argparse.ArgumentParser(description="Real Chatterbox model smoke test")
    parser.add_argument("--model", required=True, choices=sorted(MODEL_SPECS))
    parser.add_argument("--voice", required=True)
    parser.add_argument("--output-dir", default="smoke-output")
    args = parser.parse_args()

    voice = Path(args.voice)
    if not voice.exists():
        raise SystemExit(f"Reference voice does not exist: {voice}")

    output_dir = Path(args.output_dir) / args.model
    output_dir.mkdir(parents=True, exist_ok=True)

    # Use the same managed-model path as the product UI. This is intentionally
    # part of the real smoke: it validates official model download, exact local
    # snapshot selection, and upstream from_local loading instead of only testing
    # the legacy from_pretrained fallback.
    manager = LocalModelManager(output_dir / "model_state.json")
    managed = manager.download(args.model)
    if not managed.installed or not managed.snapshot_path or not managed.revision:
        raise RuntimeError("Managed model download did not produce a pinned local snapshot")

    engine = ChatterboxEngine(output_dir)
    if engine.device != "cpu":
        raise RuntimeError(f"CI smoke test expected CPU, got {engine.device_label}")
    engine.set_model_path(args.model, managed.snapshot_path)

    text = "Hello from the Creator Studio smoke test."
    if args.model in {"turbo", "nano"}:
        text = "Hello from the Creator Studio smoke test [chuckle]."

    # One real model generation plus a Studio-owned trailing digital pause tests
    # managed loading, voice conditioning, inference, saving, metadata and pause
    # insertion without doubling the expensive model inference work.
    result = engine.generate(
        script=f"{text} [pause=0.20]",
        voice_path=voice,
        model_id=args.model,
        language_id="en",
        raw_mode=False,
        smart_chunking=False,
        speech_speed=1.0,
        seed=12345,
    )

    if not result.audio_path.exists() or not result.metadata_path.exists():
        raise RuntimeError("Generation did not produce both WAV and JSON metadata")

    wav, sample_rate = torchaudio.load(result.audio_path)
    if sample_rate <= 0 or wav.numel() <= sample_rate // 10:
        raise RuntimeError("Generated WAV is unexpectedly empty or too short")
    if not torch.isfinite(wav).all():
        raise RuntimeError("Generated WAV contains NaN or infinite samples")

    quality = analyze_audio(result.audio_path)
    if quality.duration_seconds <= 0.1:
        raise RuntimeError("Quality analyzer reported an invalid duration")
    if not 0.0 <= quality.score <= 1.0:
        raise RuntimeError("Quality analyzer returned an invalid score")

    # The last 0.15 s is inside the explicit 0.20 s Studio pause and therefore
    # should be mathematically silent; this verifies that the pause was handled
    # outside the model and survived WAV serialization.
    tail = wav[..., -int(sample_rate * 0.15) :]
    if tail.numel() == 0 or float(tail.abs().max()) != 0.0:
        raise RuntimeError("Exact trailing Studio pause was not preserved as digital silence")

    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["model"]["id"] == args.model
    assert metadata["model"]["local_snapshot"] == str(Path(managed.snapshot_path).resolve())
    assert metadata["seed"] == 12345
    assert metadata["mode"] == "studio"
    assert metadata["smart_chunking"] is False
    assert metadata["chunk_count"] == 1
    assert metadata["generated_chunks"] == [text]

    # The selected version must stay pinned even if Hub refs move later. The
    # model-manager unit suite simulates a moving refs/main; here we verify the
    # real state file records the exact snapshot that actually generated audio.
    state = json.loads((output_dir / "model_state.json").read_text(encoding="utf-8"))
    assert state["models"][args.model]["revision"] == managed.revision
    assert Path(state["models"][args.model]["snapshot_path"]).resolve() == Path(managed.snapshot_path).resolve()

    # Raw mode is an engine/pipeline behavior. Exercise it with the smallest
    # model in the real-model suite so we validate the path without making every
    # matrix job perform a second expensive inference.
    if args.model == "nano":
        raw = engine.generate(
            script="Raw mode smoke test.",
            voice_path=voice,
            model_id="nano",
            language_id="en",
            raw_mode=True,
            smart_chunking=True,
            seed=24680,
        )
        raw_metadata = json.loads(raw.metadata_path.read_text(encoding="utf-8"))
        assert raw_metadata["mode"] == "raw"
        assert raw_metadata["smart_chunking"] is False
        assert raw_metadata["generated_chunks"] == ["Raw mode smoke test."]

    engine.unload()
    if engine.loaded:
        raise RuntimeError("Model still reports loaded after explicit unload")

    print(
        json.dumps(
            {
                "ok": True,
                "model": args.model,
                "managed_revision": managed.revision,
                "managed_snapshot": managed.snapshot_path,
                "sample_rate": sample_rate,
                "samples": int(wav.shape[-1]),
                "quality_score": quality.score,
                "quality_warnings": list(quality.warnings),
                "wav": str(result.audio_path),
                "metadata": str(result.metadata_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
