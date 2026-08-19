from __future__ import annotations

import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

from studio.artifact_store import ArtifactStore
from studio.model_manager import LocalModelStatus
from studio.protocol import SpeechSynthesisRequest, VoiceSourceKind
from studio.ready_voices import get_ready_voice, list_ready_voices
from studio.synthesis import SpeechSynthesisService
from studio.voice_profile_store import VoiceProfileStore


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
    def __init__(self, snapshot: Path):
        self.snapshot = snapshot

    def status(self, model_id: str) -> LocalModelStatus:
        if model_id == "kokoro-v1.0":
            return LocalModelStatus(
                model_id=model_id,
                installed=True,
                snapshot_path=str(self.snapshot),
                revision="kokoro-test-revision",
                size_gb=0.36,
                source_trusted=True,
            )
        return LocalModelStatus(model_id=model_id, installed=False, snapshot_path=None, revision=None, size_gb=0.0)


@dataclass
class FakeResult:
    audio_path: Path
    metadata_path: Path
    model_id: str = "kokoro-v1.0"
    seed: int = 0
    chunk_count: int = 1


class FakeKokoroAdapter:
    def __init__(self):
        self.calls: list[dict] = []

    def synthesize(self, **kwargs):
        self.calls.append(dict(kwargs))
        output_dir = Path(kwargs["output_dir"])
        audio = output_dir / "kokoro.wav"
        metadata = output_dir / "kokoro.json"
        _write_wav(audio)
        metadata.write_text("{}", encoding="utf-8")
        return FakeResult(audio_path=audio, metadata_path=metadata)


def test_ready_voice_catalogue_is_generic_and_curated():
    voices = list_ready_voices(engine_id="kokoro", language="en")
    assert voices
    assert len({voice.voice_id for voice in voices}) == len(voices)
    assert all(voice.engine_id == "kokoro" and voice.language == "en" for voice in voices)
    assert get_ready_voice("kokoro", "af_heart").recommended is True
    assert get_ready_voice("kokoro", "bf_emma").locale == "en-GB"


def test_ready_voice_routes_through_kokoro_without_clone_reference(tmp_path: Path):
    data = tmp_path / "data" / "speech-core"
    profiles = VoiceProfileStore(data / "voice-profiles")
    artifacts = ArtifactStore(data / "artifacts")
    profiles.create(
        "heart",
        "Heart",
        source_kind=VoiceSourceKind.READY,
        source_voice_id="af_heart",
        supported_languages=("en",),
    )
    snapshot = tmp_path / "models" / "kokoro"
    snapshot.mkdir(parents=True)
    adapter = FakeKokoroAdapter()

    service = SpeechSynthesisService(
        data,
        profile_store=profiles,
        artifact_store=artifacts,
        model_manager=FakeModelManager(snapshot),
        engine_factory=lambda _output: (_ for _ in ()).throw(AssertionError("Chatterbox must not load")),
        kokoro_adapter=adapter,
    )
    result = service.synthesize(
        SpeechSynthesisRequest(
            text="This is a ready voice from the reusable Speech Core.",
            voice_profile_id="heart",
            language="en",
            engine_override="kokoro",
        )
    )

    assert result.provenance.engine_id == "kokoro"
    assert result.provenance.model_id == "kokoro-v1.0"
    assert result.provenance.model_revision == "kokoro-test-revision"
    assert result.metadata["source_kind"] == "ready"
    assert result.metadata["engine_voice_id"] == "af_heart"
    assert result.audio.uri.startswith("local://artifacts/")
    assert artifacts.resolve(result.audio).is_file()
    assert adapter.calls and adapter.calls[0]["voice_id"] == "af_heart"
    assert not any(service.work_dir.iterdir())
