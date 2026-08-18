from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
import torchaudio

from studio.artifact_store import ArtifactStore
from studio.engine import NativeChatterboxEngine
from studio.model_manager import LocalModelManager
from studio.models import MODEL_SPECS
from studio.protocol import EngineBinding, SpeechSynthesisRequest, VoiceSourceKind
from studio.quality import analyze_audio
from studio.synthesis import SpeechSynthesisService, SynthesisExecutionSettings
from studio.voice_profile_store import VoiceProfileStore


ENGINE_FOR_MODEL = {
    "multilingual-v3": "chatterbox-v3",
    "turbo": "chatterbox-turbo",
    "nano": "chatterbox-nano",
}


def _validate_wav(path: Path) -> tuple[torch.Tensor, int]:
    wav, sample_rate = torchaudio.load(path)
    if sample_rate <= 0 or wav.numel() <= sample_rate // 10:
        raise RuntimeError("Generated WAV is unexpectedly empty or too short")
    if not torch.isfinite(wav).all():
        raise RuntimeError("Generated WAV contains NaN or infinite samples")
    return wav, sample_rate


def _validate_exact_tail_pause(wav: torch.Tensor, sample_rate: int) -> None:
    tail = wav[..., -int(sample_rate * 0.15) :]
    if tail.numel() == 0 or float(tail.abs().max()) != 0.0:
        raise RuntimeError("Exact trailing Studio pause was not preserved as digital silence")


def main() -> None:
    parser = argparse.ArgumentParser(description="Real Chatterbox Speech Core smoke test")
    parser.add_argument("--model", required=True, choices=sorted(MODEL_SPECS))
    parser.add_argument("--voice", required=True)
    parser.add_argument("--output-dir", default="smoke-output")
    args = parser.parse_args()

    voice = Path(args.voice).resolve()
    if not voice.exists():
        raise SystemExit(f"Reference voice does not exist: {voice}")

    model_id = args.model
    engine_id = ENGINE_FOR_MODEL[model_id]
    output_dir = Path(args.output_dir).resolve() / model_id
    output_dir.mkdir(parents=True, exist_ok=True)

    manager = LocalModelManager(output_dir / "model_state.json")
    managed = manager.download(model_id, progress=lambda current, total, desc: None)
    if not managed.installed or not managed.snapshot_path or not managed.revision:
        raise RuntimeError("Managed model download did not produce a pinned local snapshot")

    core_dir = output_dir / "speech-core"
    artifacts = ArtifactStore(core_dir / "artifacts")
    profiles = VoiceProfileStore(core_dir / "voice-profiles")
    reference = artifacts.register_file(
        voice,
        artifact_id="smoke-reference",
        mime_type="audio/wav",
        copy=True,
    )
    profiles.create(
        "smoke-voice",
        "Smoke Voice",
        source_kind=VoiceSourceKind.CLONE,
        reference=reference,
        supported_languages=("en",),
    )
    profiles.add_binding(
        "smoke-voice",
        EngineBinding(
            engine_id=engine_id,
            model_id=model_id,
            model_revision=managed.revision,
            certified_languages=("en",),
        ),
        promote_revision=True,
    )

    service = SpeechSynthesisService(core_dir)
    text = "Hello from the Creator Studio Speech Core smoke test."
    if model_id in {"turbo", "nano"}:
        text = "Hello from the Creator Studio Speech Core smoke test [chuckle]."
    script = f"{text} [pause=0.20]"

    progress_events: list[tuple[str, int | None, int | None]] = []
    speech = service.synthesize(
        SpeechSynthesisRequest(
            text=script,
            voice_profile_id="smoke-voice",
            language="en",
            engine_override=engine_id,
        ),
        execution=SynthesisExecutionSettings(
            seed=12345,
            device="cpu",
            device_label="CPU",
            raw_mode=False,
            smart_chunking=False,
            speech_speed=1.0,
        ),
        progress_callback=lambda stage, current, total: progress_events.append((stage, current, total)),
    )

    generated = artifacts.resolve(speech.audio)
    wav, sample_rate = _validate_wav(generated)
    _validate_exact_tail_pause(wav, sample_rate)
    quality = analyze_audio(generated)
    if quality.duration_seconds <= 0.1:
        raise RuntimeError("Quality analyzer reported an invalid duration")
    if not 0.0 <= quality.score <= 1.0:
        raise RuntimeError("Quality analyzer returned an invalid score")

    if speech.provenance.engine_id != engine_id:
        raise RuntimeError("Speech Core selected the wrong engine during an explicit real-model smoke")
    if speech.provenance.model_id != model_id:
        raise RuntimeError("Speech Core reported the wrong model")
    if speech.provenance.model_revision != managed.revision:
        raise RuntimeError("Speech Core did not preserve the exact managed model revision")
    if speech.metadata.get("seed") != 12345 or speech.metadata.get("chunk_count") != 1:
        raise RuntimeError("Speech Core did not preserve stable seed/chunk metadata")
    serialized_speech = speech.to_dict()
    if str(output_dir) in json.dumps(serialized_speech):
        raise RuntimeError("Public SpeechArtifact leaked a private local path")
    if not any(current == 1 for _stage, current, _total in progress_events):
        raise RuntimeError("Speech Core did not forward engine progress")

    state = json.loads((output_dir / "model_state.json").read_text(encoding="utf-8"))
    if state["models"][model_id]["revision"] != managed.revision:
        raise RuntimeError("Model state did not preserve the exact revision used by Core")
    if Path(state["models"][model_id]["snapshot_path"]).resolve() != Path(managed.snapshot_path).resolve():
        raise RuntimeError("Model state did not preserve the exact snapshot path used by Core")

    # Nano performs one explicit native-vs-Core parity inference. The public
    # `ChatterboxEngine` name is Core-backed in Phase 3, so direct parity must name the
    # native implementation deliberately rather than accidentally bypassing Core.
    parity: dict[str, object] | None = None
    if model_id == "nano":
        direct_dir = output_dir / "direct-parity"
        direct = NativeChatterboxEngine(direct_dir)
        if direct.device != "cpu":
            raise RuntimeError(f"CI parity expected CPU, got {direct.device_label}")
        direct.set_model_path(model_id, managed.snapshot_path)
        direct_result = direct.generate(
            script=script,
            voice_path=voice,
            model_id=model_id,
            language_id="en",
            raw_mode=False,
            smart_chunking=False,
            speech_speed=1.0,
            seed=12345,
        )
        direct_wav, direct_rate = _validate_wav(direct_result.audio_path)
        _validate_exact_tail_pause(direct_wav, direct_rate)
        direct_metadata = json.loads(direct_result.metadata_path.read_text(encoding="utf-8"))
        if direct_result.model_id != speech.provenance.model_id:
            raise RuntimeError("Direct/Core parity disagreed on model identity")
        if direct_result.seed != speech.metadata["seed"]:
            raise RuntimeError("Direct/Core parity disagreed on seed")
        if direct_result.chunk_count != speech.metadata["chunk_count"]:
            raise RuntimeError("Direct/Core parity disagreed on chunk count")
        if direct_metadata["mode"] != "studio" or direct_metadata["smart_chunking"] is not False:
            raise RuntimeError("Direct path did not preserve expected Studio semantics")
        duration_delta = abs((direct_wav.shape[-1] / direct_rate) - speech.duration_seconds)
        if duration_delta > 0.25:
            raise RuntimeError(f"Direct/Core generated duration drifted unexpectedly ({duration_delta:.3f}s)")
        direct.unload()
        parity = {
            "checked": True,
            "duration_delta_seconds": round(duration_delta, 4),
            "direct_chunk_count": direct_result.chunk_count,
        }

    print(
        json.dumps(
            {
                "ok": True,
                "path": "speech-core",
                "engine": engine_id,
                "model": model_id,
                "managed_revision": managed.revision,
                "sample_rate": sample_rate,
                "samples": int(wav.shape[-1]),
                "quality_score": quality.score,
                "quality_warnings": list(quality.warnings),
                "artifact": serialized_speech["audio"],
                "provenance": serialized_speech["provenance"],
                "progress_events": len(progress_events),
                "nano_direct_parity": parity,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
