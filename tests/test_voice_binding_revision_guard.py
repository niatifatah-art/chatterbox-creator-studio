from __future__ import annotations

from pathlib import Path

import pytest

from studio.artifact_store import ArtifactStore
from studio.model_manager import LocalModelStatus
from studio.protocol import EngineBinding, SpeechErrorKind, SpeechSynthesisRequest, VoiceSourceKind
from studio.synthesis import SpeechSynthesisService, SynthesisError
from studio.voice_profile_store import VoiceProfileStore


class SelectedRevisionManager:
    def __init__(self, snapshot: Path):
        self.snapshot = snapshot

    def status(self, model_id: str) -> LocalModelStatus:
        self.snapshot.mkdir(parents=True, exist_ok=True)
        return LocalModelStatus(
            model_id=model_id,
            installed=True,
            snapshot_path=str(self.snapshot),
            revision="new-selected-revision",
            size_gb=1.0,
        )


def test_consistency_binding_fails_closed_when_selected_model_revision_drifted(tmp_path: Path):
    data = tmp_path / "speech-core"
    artifacts = ArtifactStore(data / "artifacts")
    profiles = VoiceProfileStore(data / "voice-profiles")

    reference_file = tmp_path / "reference.wav"
    reference_file.write_bytes(b"reference-is-not-read-before-revision-check")
    reference = artifacts.register_file(reference_file, artifact_id="reference", mime_type="audio/wav")
    profiles.create(
        "voice",
        "Voice",
        source_kind=VoiceSourceKind.CLONE,
        reference=reference,
        supported_languages=("en",),
    )
    profiles.add_binding(
        "voice",
        EngineBinding(
            engine_id="chatterbox-v3",
            model_id="multilingual-v3",
            model_revision="certified-old-revision",
        ),
        promote_revision=True,
    )

    engine_loaded = False

    def forbidden_engine(_output_dir: Path):
        nonlocal engine_loaded
        engine_loaded = True
        raise AssertionError("Engine must not load when the pinned model revision is unavailable")

    service = SpeechSynthesisService(
        data,
        profile_store=profiles,
        artifact_store=artifacts,
        model_manager=SelectedRevisionManager(tmp_path / "selected-model"),
        engine_factory=forbidden_engine,
    )

    with pytest.raises(SynthesisError) as exc:
        service.synthesize(
            SpeechSynthesisRequest(
                text="Hello",
                voice_profile_id="voice",
                language="en",
            )
        )

    assert exc.value.kind == SpeechErrorKind.MODEL_NOT_INSTALLED
    assert exc.value.data["required_revision"] == "certified-old-revision"
    assert exc.value.data["selected_revision"] == "new-selected-revision"
    assert engine_loaded is False
