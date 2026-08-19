from __future__ import annotations

import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

from studio.artifact_store import ArtifactStore
from studio.model_manager import LocalModelStatus
from studio.protocol import SpeechSynthesisRequest, VoiceSourceKind
from studio.ready_voices import create_ready_voice_profile, get_ready_voice, list_ready_voices
from studio.synthesis import SpeechSynthesisService
from studio.voice_profile_store import VoiceProfileStore


QWEN_REVISIONS = {
    "qwen3-0.6b-base": "qwen-base-test-revision",
    "qwen3-0.6b-custom": "qwen-custom-test-revision",
    "qwen3-1.7b-voice-design": "qwen-design-test-revision",
}


def _write_wav(path: Path, seconds: float = 0.25, sample_rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        for index in range(frames):
            sample = int(3500 * math.sin(2 * math.pi * 220 * index / sample_rate))
            handle.writeframesraw(struct.pack("<h", sample))


class FakeModelManager:
    def __init__(self, snapshots: dict[str, Path]):
        self.snapshots = snapshots

    def status(self, model_id: str) -> LocalModelStatus:
        snapshot = self.snapshots.get(model_id)
        if snapshot is None:
            return LocalModelStatus(
                model_id=model_id,
                installed=False,
                snapshot_path=None,
                revision=None,
                size_gb=0.0,
            )
        return LocalModelStatus(
            model_id=model_id,
            installed=True,
            snapshot_path=str(snapshot),
            revision=QWEN_REVISIONS[model_id],
            size_gb=1.0,
            source_trusted=True,
        )


@dataclass
class FakeResult:
    audio_path: Path
    metadata_path: Path
    model_id: str
    seed: int = 0
    chunk_count: int = 1


class FakeQwenAdapter:
    def __init__(self):
        self.calls: list[dict] = []

    def synthesize(self, **kwargs):
        self.calls.append(dict(kwargs))
        output_dir = Path(kwargs["output_dir"])
        audio = output_dir / "qwen.wav"
        metadata = output_dir / "qwen.json"
        _write_wav(audio)
        metadata.write_text("{}", encoding="utf-8")
        return FakeResult(
            audio_path=audio,
            metadata_path=metadata,
            model_id=kwargs["model_id"],
        )


def _snapshot(tmp_path: Path, model_id: str) -> Path:
    path = tmp_path / "models" / model_id
    path.mkdir(parents=True)
    return path


def test_qwen_ready_voice_catalogue_has_only_the_official_nine_speakers():
    english = list_ready_voices(engine_id="qwen3-ready", language="en")
    french = list_ready_voices(engine_id="qwen3-ready", language="fr")
    assert len(english) == 9
    assert {voice.voice_id for voice in english} == {voice.voice_id for voice in french}
    assert get_ready_voice("qwen3-ready", "Ryan").recommended is True
    assert get_ready_voice("qwen3-ready", "Ono_Anna").language == "ja"


def test_qwen_ready_voice_routes_without_provider_specific_caller_input(tmp_path: Path):
    data = tmp_path / "data" / "speech-core"
    profiles = VoiceProfileStore(data / "voice-profiles")
    artifacts = ArtifactStore(data / "artifacts")
    revision = QWEN_REVISIONS["qwen3-0.6b-custom"]
    create_ready_voice_profile(
        profiles,
        "ryan",
        engine_id="qwen3-ready",
        voice_id="Ryan",
        model_id="qwen3-0.6b-custom",
        model_revision=revision,
    )
    adapter = FakeQwenAdapter()
    service = SpeechSynthesisService(
        data,
        profile_store=profiles,
        artifact_store=artifacts,
        model_manager=FakeModelManager({"qwen3-0.6b-custom": _snapshot(tmp_path, "qwen3-0.6b-custom")}),
        engine_factory=lambda _output: (_ for _ in ()).throw(AssertionError("Chatterbox must not load")),
        qwen_adapter=adapter,
    )

    result = service.synthesize(
        SpeechSynthesisRequest(
            text="A stable ready voice should not require provider knowledge from the caller.",
            voice_profile_id="ryan",
            language="en",
        )
    )

    assert result.provenance.engine_id == "qwen3-ready"
    assert result.provenance.model_id == "qwen3-0.6b-custom"
    assert result.provenance.model_revision == revision
    assert result.metadata["engine_voice_id"] == "Ryan"
    assert adapter.calls and adapter.calls[0]["voice_id"] == "Ryan"
    assert adapter.calls[0]["reference_audio"] is None
    assert artifacts.resolve(result.audio).is_file()


def test_qwen_clone_uses_local_reference_and_optional_reference_transcript(tmp_path: Path):
    data = tmp_path / "data" / "speech-core"
    profiles = VoiceProfileStore(data / "voice-profiles")
    artifacts = ArtifactStore(data / "artifacts")
    reference_wav = tmp_path / "reference.wav"
    _write_wav(reference_wav)
    reference = artifacts.register_file(
        reference_wav,
        artifact_id="qwen-reference",
        mime_type="audio/wav",
        copy=True,
    )
    profiles.create(
        "clone",
        "Clone",
        source_kind=VoiceSourceKind.CLONE,
        reference=reference,
        supported_languages=("en",),
        metadata={"reference_text": "This is the reference transcript."},
    )
    adapter = FakeQwenAdapter()
    service = SpeechSynthesisService(
        data,
        profile_store=profiles,
        artifact_store=artifacts,
        model_manager=FakeModelManager({"qwen3-0.6b-base": _snapshot(tmp_path, "qwen3-0.6b-base")}),
        engine_factory=lambda _output: (_ for _ in ()).throw(AssertionError("Chatterbox must not load")),
        qwen_adapter=adapter,
    )

    result = service.synthesize(
        SpeechSynthesisRequest(
            text="Clone this voice through the reusable Speech Core.",
            voice_profile_id="clone",
            language="en",
            engine_override="qwen3-clone",
        )
    )

    assert result.provenance.engine_id == "qwen3-clone"
    assert result.provenance.model_id == "qwen3-0.6b-base"
    call = adapter.calls[0]
    assert Path(call["reference_audio"]).is_file()
    assert call["reference_text"] == "This is the reference transcript."
    assert call["voice_id"] is None


def test_qwen_voice_design_uses_semantic_profile_description(tmp_path: Path):
    data = tmp_path / "data" / "speech-core"
    profiles = VoiceProfileStore(data / "voice-profiles")
    artifacts = ArtifactStore(data / "artifacts")
    description = "A warm, energetic young narrator with clear articulation and a light smile."
    profiles.create(
        "designed",
        "Designed Voice",
        source_kind=VoiceSourceKind.DESIGNED,
        description=description,
        supported_languages=("en",),
    )
    adapter = FakeQwenAdapter()
    service = SpeechSynthesisService(
        data,
        profile_store=profiles,
        artifact_store=artifacts,
        model_manager=FakeModelManager(
            {"qwen3-1.7b-voice-design": _snapshot(tmp_path, "qwen3-1.7b-voice-design")}
        ),
        engine_factory=lambda _output: (_ for _ in ()).throw(AssertionError("Chatterbox must not load")),
        qwen_adapter=adapter,
    )

    result = service.synthesize(
        SpeechSynthesisRequest(
            text="This voice is designed from a semantic description.",
            voice_profile_id="designed",
            language="en",
        )
    )

    assert result.provenance.engine_id == "qwen3-voice-design"
    assert result.provenance.model_id == "qwen3-1.7b-voice-design"
    assert adapter.calls[0]["instruct"] == description
    assert adapter.calls[0]["reference_audio"] is None
