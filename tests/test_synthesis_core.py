from __future__ import annotations

import json
import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

import pytest

from studio.artifact_store import ArtifactStore
from studio.cancellation import clear_generation_cancel, request_generation_cancel
from studio.model_manager import LocalModelStatus
from studio.protocol import (
    EngineBinding,
    Priority,
    SpeechErrorKind,
    SpeechEvent,
    SpeechSynthesisRequest,
    VoiceSourceKind,
)
from studio.synthesis import SpeechSynthesisService, SynthesisError, SynthesisExecutionSettings
from studio.voice_profile_store import VoiceProfileStore


def _write_wav(path: Path, seconds: float = 0.2, sample_rate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        for index in range(frames):
            sample = int(5000 * math.sin(2 * math.pi * 220 * index / sample_rate))
            handle.writeframesraw(struct.pack("<h", sample))


class FakeModelManager:
    def __init__(self, root: Path, installed: tuple[str, ...] = ("multilingual-v3",)):
        self.root = root
        self.installed = set(installed)
        self.status_calls: list[str] = []
        self.download_called = False

    def status(self, model_id: str) -> LocalModelStatus:
        self.status_calls.append(model_id)
        if model_id in self.installed:
            path = self.root / model_id
            path.mkdir(parents=True, exist_ok=True)
            return LocalModelStatus(
                model_id=model_id,
                installed=True,
                snapshot_path=str(path),
                revision=f"rev-{model_id}",
                size_gb=1.0,
            )
        return LocalModelStatus(
            model_id=model_id,
            installed=False,
            snapshot_path=None,
            revision=None,
            size_gb=0.0,
        )

    def download(self, *_args, **_kwargs):  # pragma: no cover - should never be called
        self.download_called = True
        raise AssertionError("Synthesis must never download a model implicitly")


@dataclass
class FakeResult:
    audio_path: Path
    metadata_path: Path
    seed: int
    chunk_count: int
    model_id: str


class FakeEngine:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.device = "cpu"
        self.device_label = "CPU"
        self.model_paths: dict[str, str] = {}
        self.calls: list[dict] = []
        self.unloaded = False

    def set_device(self, device: str, label: str | None = None) -> None:
        self.device = device
        self.device_label = label or device

    def set_model_path(self, model_id: str, path: str | Path | None) -> None:
        self.model_paths[model_id] = str(path) if path is not None else ""

    def generate(self, **kwargs):
        self.calls.append(dict(kwargs))
        progress = kwargs.get("progress_callback")
        if callable(progress):
            progress("Generating speech…", 0, 1)
        audio = self.output_dir / "generated.wav"
        _write_wav(audio, seconds=0.35)
        metadata = self.output_dir / "generated.json"
        metadata.write_text(
            json.dumps({"text": kwargs["script"], "voice_path": str(kwargs["voice_path"])}),
            encoding="utf-8",
        )
        if callable(progress):
            progress("Generating speech…", 1, 1)
        return FakeResult(
            audio_path=audio,
            metadata_path=metadata,
            seed=int(kwargs.get("seed") or 1234),
            chunk_count=1,
            model_id=str(kwargs["model_id"]),
        )

    def unload(self) -> None:
        self.unloaded = True


def _service(tmp_path: Path, *, installed=("multilingual-v3",)):
    data = tmp_path / "data" / "speech-core"
    profiles = VoiceProfileStore(data / "voice-profiles")
    artifacts = ArtifactStore(data / "artifacts")
    reference_file = tmp_path / "reference.wav"
    _write_wav(reference_file)
    reference = artifacts.register_file(reference_file, artifact_id="voice-reference", mime_type="audio/wav")
    profiles.create(
        "creator-voice",
        "Creator Voice",
        source_kind=VoiceSourceKind.CLONE,
        reference=reference,
        supported_languages=("en", "ar"),
    )
    manager = FakeModelManager(tmp_path / "models", installed=tuple(installed))
    engines: list[FakeEngine] = []

    def factory(output_dir: Path):
        engine = FakeEngine(output_dir)
        engines.append(engine)
        return engine

    service = SpeechSynthesisService(
        data,
        profile_store=profiles,
        artifact_store=artifacts,
        model_manager=manager,
        engine_factory=factory,
    )
    return service, profiles, artifacts, manager, engines


def test_core_synthesis_routes_arabic_clone_and_returns_private_logical_artifact(tmp_path: Path):
    service, _profiles, artifacts, manager, engines = _service(tmp_path)
    request = SpeechSynthesisRequest(
        text="مرحبا هذا اختبار للصوت",
        voice_profile_id="creator-voice",
        language="auto",
        engine_override="chatterbox-v3",
    )

    artifact = service.synthesize(
        request,
        execution=SynthesisExecutionSettings(seed=42, device="cpu", device_label="CPU"),
    )

    assert artifact.language == "ar"
    assert artifact.voice_profile_id == "creator-voice"
    assert artifact.provenance.engine_id == "chatterbox-v3"
    assert artifact.provenance.model_id == "multilingual-v3"
    assert artifact.provenance.model_revision == "rev-multilingual-v3"
    assert artifact.metadata["seed"] == 42
    assert artifact.metadata["chunk_count"] == 1
    assert artifact.audio.uri.startswith("local://artifacts/")
    assert str(tmp_path.resolve()) not in json.dumps(artifact.to_dict())
    assert artifacts.resolve(artifact.audio).is_file()
    assert 0.30 < artifact.duration_seconds < 0.40
    assert manager.download_called is False
    assert engines and engines[0].calls[0]["language_id"] == "ar"
    assert engines[0].calls[0]["seed"] == 42
    assert engines[0].calls[0]["script"] == request.text
    assert engines[0].calls[0]["model_id"] == "multilingual-v3"
    assert engines[0].unloaded is True
    assert not any(service.work_dir.iterdir())


def test_core_synthesis_can_target_each_current_chatterbox_route_without_new_service_branch(tmp_path: Path):
    service, _profiles, _artifacts, _manager, engines = _service(
        tmp_path,
        installed=("multilingual-v3", "turbo", "nano"),
    )
    for engine_id, model_id in (
        ("chatterbox-v3", "multilingual-v3"),
        ("chatterbox-turbo", "turbo"),
        ("chatterbox-nano", "nano"),
    ):
        result = service.synthesize(
            SpeechSynthesisRequest(
                text="Hello from the reusable Speech Core.",
                voice_profile_id="creator-voice",
                language="en",
                engine_override=engine_id,
            )
        )
        assert result.provenance.engine_id == engine_id
        assert result.provenance.model_id == model_id
    assert len(engines) == 3
    assert [engine.calls[0]["model_id"] for engine in engines] == ["multilingual-v3", "turbo", "nano"]


def test_missing_model_is_structured_and_never_downloads(tmp_path: Path):
    service, _profiles, _artifacts, manager, engines = _service(tmp_path, installed=())
    with pytest.raises(SynthesisError) as exc:
        service.synthesize(
            SpeechSynthesisRequest(
                text="Hello",
                voice_profile_id="creator-voice",
                language="en",
                engine_override="chatterbox-v3",
            )
        )
    assert exc.value.kind == SpeechErrorKind.MODEL_NOT_INSTALLED
    assert exc.value.data["model_id"] == "multilingual-v3"
    assert manager.download_called is False
    assert engines == []


def test_voice_revision_mismatch_fails_before_engine_load(tmp_path: Path):
    service, _profiles, _artifacts, _manager, engines = _service(tmp_path)
    with pytest.raises(SynthesisError) as exc:
        service.synthesize(
            SpeechSynthesisRequest(
                text="Hello",
                voice_profile_id="creator-voice",
                voice_revision=99,
                engine_override="chatterbox-v3",
            )
        )
    assert exc.value.kind == SpeechErrorKind.INVALID_ARGUMENT
    assert engines == []


def test_tampered_voice_reference_is_not_sent_to_engine(tmp_path: Path):
    service, profiles, artifacts, _manager, engines = _service(tmp_path)
    record = profiles.get("creator-voice")
    assert record is not None and record.profile.source.reference is not None
    artifacts.resolve(record.profile.source.reference).write_bytes(b"tampered")

    with pytest.raises(SynthesisError) as exc:
        service.synthesize(
            SpeechSynthesisRequest(
                text="Hello",
                voice_profile_id="creator-voice",
                engine_override="chatterbox-v3",
            )
        )
    assert exc.value.kind == SpeechErrorKind.VOICE_REFERENCE_MISSING
    assert engines == []


def test_structured_events_are_rejected_instead_of_silently_ignored(tmp_path: Path):
    service, *_ = _service(tmp_path)
    with pytest.raises(SynthesisError) as exc:
        service.synthesize(
            SpeechSynthesisRequest(
                text="Hello",
                voice_profile_id="creator-voice",
                engine_override="chatterbox-v3",
                events=(SpeechEvent(kind="pause", value=0.5, position=5),),
            )
        )
    assert exc.value.kind == SpeechErrorKind.INVALID_ARGUMENT


def test_unbound_ready_voice_fails_closed_instead_of_guessing_an_engine(tmp_path: Path):
    data = tmp_path / "data" / "speech-core"
    profiles = VoiceProfileStore(data / "voice-profiles")
    profiles.create("ready", "Legacy Ready", source_kind=VoiceSourceKind.READY, source_voice_id="af_heart")
    service = SpeechSynthesisService(
        data,
        profile_store=profiles,
        artifact_store=ArtifactStore(data / "artifacts"),
        model_manager=FakeModelManager(tmp_path / "models", installed=("multilingual-v3",)),
        engine_factory=lambda _output: (_ for _ in ()).throw(AssertionError("Chatterbox engine must not load")),
    )
    with pytest.raises(SynthesisError) as exc:
        service.synthesize(SpeechSynthesisRequest(text="Hello", voice_profile_id="ready", language="en"))
    assert exc.value.kind == SpeechErrorKind.ENGINE_UNAVAILABLE


def test_catalogued_future_engine_cannot_execute_through_chatterbox_factory(tmp_path: Path):
    service, _profiles, _artifacts, _manager, engines = _service(tmp_path)
    with pytest.raises(SynthesisError) as exc:
        service.synthesize(
            SpeechSynthesisRequest(
                text="Hello",
                voice_profile_id="creator-voice",
                language="en",
                engine_override="qwen3-tts",
            )
        )
    assert exc.value.kind == SpeechErrorKind.ENGINE_UNAVAILABLE
    assert exc.value.data["engine_id"] == "qwen3-tts"
    assert engines == []


def test_preferred_engine_pin_is_respected_for_consistency_voice(tmp_path: Path):
    service, profiles, _artifacts, _manager, _engines = _service(
        tmp_path,
        installed=("multilingual-v3", "turbo", "nano"),
    )
    profiles.add_binding("creator-voice", EngineBinding(engine_id="chatterbox-nano", model_id="nano"))
    profiles.set_preferred_engine("creator-voice", "chatterbox-nano")
    result = service.synthesize(
        SpeechSynthesisRequest(
            text="Hello",
            voice_profile_id="creator-voice",
            language="en",
            priority=Priority.CONSISTENCY,
        )
    )
    assert result.provenance.engine_id == "chatterbox-nano"


def test_raw_mode_is_forwarded_but_public_chunking_metadata_reflects_effective_behavior(tmp_path: Path):
    service, _profiles, _artifacts, _manager, engines = _service(tmp_path)
    result = service.synthesize(
        SpeechSynthesisRequest(
            text="Raw mode smoke.",
            voice_profile_id="creator-voice",
            engine_override="chatterbox-v3",
        ),
        execution=SynthesisExecutionSettings(raw_mode=True, smart_chunking=True, seed=24680),
    )
    assert engines and engines[0].calls[0]["raw_mode"] is True
    assert engines[0].calls[0]["smart_chunking"] is True
    assert engines[0].calls[0]["seed"] == 24680
    assert result.metadata["raw_mode"] is True
    assert result.metadata["smart_chunking"] is False


def test_core_forwards_progress_and_maps_cooperative_cancel(tmp_path: Path):
    service, _profiles, _artifacts, _manager, engines = _service(tmp_path)
    progress: list[tuple[str, int | None, int | None]] = []
    result = service.synthesize(
        SpeechSynthesisRequest(
            text="Hello",
            voice_profile_id="creator-voice",
            engine_override="chatterbox-v3",
        ),
        progress_callback=lambda stage, current, total: progress.append((stage, current, total)),
    )
    assert result.audio.size_bytes and result.audio.size_bytes > 0
    assert any(stage == "Generating speech…" and current == 1 and total == 1 for stage, current, total in progress)
    assert engines and engines[0].unloaded is True

    clear_generation_cancel()
    request_generation_cancel()
    try:
        with pytest.raises(SynthesisError) as exc:
            service.synthesize(
                SpeechSynthesisRequest(
                    text="Cancelled",
                    voice_profile_id="creator-voice",
                    engine_override="chatterbox-v3",
                )
            )
        assert exc.value.kind == SpeechErrorKind.CANCELLED
    finally:
        clear_generation_cancel()
