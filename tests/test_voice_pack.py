from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from studio.artifact_store import ArtifactStore
from studio.protocol import EngineBinding, VoiceSourceKind
from studio.voice_pack import VoicePackError, export_voice_pack, import_voice_pack
from studio.voice_profile_store import VoiceProfileStore


def _stores(root: Path):
    return VoiceProfileStore(root / "voice-profiles"), ArtifactStore(root / "artifacts")


def test_voice_pack_round_trip_preserves_identity_and_owned_artifacts(tmp_path: Path):
    source_profiles, source_artifacts = _stores(tmp_path / "source")
    reference_file = tmp_path / "reference.wav"
    prompt_file = tmp_path / "conditioning.bin"
    reference_file.write_bytes(b"RIFF-reference")
    prompt_file.write_bytes(b"conditioning")
    reference = source_artifacts.register_file(reference_file, artifact_id="voice-reference", mime_type="audio/wav")
    prompt = source_artifacts.register_file(prompt_file, artifact_id="engine-prompt", mime_type="application/octet-stream")

    source_profiles.create(
        "صوت-رئيسي",
        "صوت رئيسي",
        source_kind=VoiceSourceKind.CLONE,
        reference=reference,
        supported_languages=("ar", "en"),
        pronunciation_hints={"Qwen": "Q-wen"},
    )
    source_profiles.add_binding(
        "صوت-رئيسي",
        EngineBinding(
            engine_id="future-engine",
            model_id="candidate-v1",
            model_revision="abc123",
            recipe_id="golden",
            prompt_artifact=prompt,
            certified_languages=("ar",),
        ),
    )

    pack = export_voice_pack(
        "صوت-رئيسي",
        profile_store=source_profiles,
        artifact_store=source_artifacts,
        destination=tmp_path / "voice.voicepack",
    )
    assert pack.is_file()

    with zipfile.ZipFile(pack) as archive:
        names = archive.namelist()
        assert "manifest.json" in names
        assert all("model" not in name.casefold() for name in names if name != "manifest.json")
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == "creator-studio.voicepack"
        assert "/home/" not in json.dumps(manifest)
        assert "C:\\" not in json.dumps(manifest)

    target_profiles, target_artifacts = _stores(tmp_path / "target")
    imported = import_voice_pack(
        pack,
        profile_store=target_profiles,
        artifact_store=target_artifacts,
    )
    assert imported.profile.profile_id == "صوت-رئيسي"
    assert imported.profile.display_name == "صوت رئيسي"
    assert imported.profile.pronunciation_hints["Qwen"] == "Q-wen"
    assert imported.profile.source.reference is not None
    assert target_artifacts.resolve(imported.profile.source.reference).read_bytes() == b"RIFF-reference"
    binding = imported.binding_for("future-engine")
    assert binding is not None and binding.prompt_artifact is not None
    assert target_artifacts.resolve(binding.prompt_artifact).read_bytes() == b"conditioning"


def test_voice_pack_import_refuses_profile_collision_by_default(tmp_path: Path):
    profiles, artifacts = _stores(tmp_path / "source")
    profiles.create("voice", "Voice", source_kind=VoiceSourceKind.READY, source_voice_id="preset")
    pack = export_voice_pack("voice", profile_store=profiles, artifact_store=artifacts, destination=tmp_path / "voice.voicepack")

    target_profiles, target_artifacts = _stores(tmp_path / "target")
    target_profiles.create("voice", "Existing", source_kind=VoiceSourceKind.READY, source_voice_id="other")
    with pytest.raises(FileExistsError):
        import_voice_pack(pack, profile_store=target_profiles, artifact_store=target_artifacts)


def test_voice_pack_can_import_same_identity_under_explicit_new_id(tmp_path: Path):
    profiles, artifacts = _stores(tmp_path / "source")
    profiles.create("voice", "Voice", source_kind=VoiceSourceKind.READY, source_voice_id="preset")
    pack = export_voice_pack("voice", profile_store=profiles, artifact_store=artifacts, destination=tmp_path / "voice.voicepack")

    target_profiles, target_artifacts = _stores(tmp_path / "target")
    target_profiles.create("voice", "Existing", source_kind=VoiceSourceKind.READY, source_voice_id="other")
    imported = import_voice_pack(
        pack,
        profile_store=target_profiles,
        artifact_store=target_artifacts,
        profile_id_override="voice-imported",
    )
    assert imported.profile.profile_id == "voice-imported"


def test_voice_pack_rejects_tampered_artifact_and_rolls_back_new_files(tmp_path: Path):
    profiles, artifacts = _stores(tmp_path / "source")
    reference_file = tmp_path / "reference.wav"
    reference_file.write_bytes(b"original")
    ref = artifacts.register_file(reference_file, artifact_id="ref", mime_type="audio/wav")
    profiles.create("voice", "Voice", source_kind=VoiceSourceKind.CLONE, reference=ref)
    pack = export_voice_pack("voice", profile_store=profiles, artifact_store=artifacts, destination=tmp_path / "voice.voicepack")

    tampered = tmp_path / "tampered.voicepack"
    with zipfile.ZipFile(pack) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename.startswith("artifacts/"):
                data = b"tampered"
            target.writestr(info.filename, data)

    target_profiles, target_artifacts = _stores(tmp_path / "target")
    with pytest.raises(VoicePackError, match="integrity|wrong size"):
        import_voice_pack(tampered, profile_store=target_profiles, artifact_store=target_artifacts)
    assert target_profiles.list_ids() == ()
    assert list((tmp_path / "target" / "artifacts").iterdir()) == []


def test_voice_pack_rejects_unexpected_archive_members(tmp_path: Path):
    profiles, artifacts = _stores(tmp_path / "source")
    profiles.create("voice", "Voice", source_kind=VoiceSourceKind.READY, source_voice_id="preset")
    pack = export_voice_pack("voice", profile_store=profiles, artifact_store=artifacts, destination=tmp_path / "voice.voicepack")

    unexpected = tmp_path / "unexpected.voicepack"
    with zipfile.ZipFile(pack) as source, zipfile.ZipFile(unexpected, "w") as target:
        for info in source.infolist():
            target.writestr(info.filename, source.read(info.filename))
        target.writestr("../surprise.txt", b"nope")

    target_profiles, target_artifacts = _stores(tmp_path / "target")
    with pytest.raises(VoicePackError, match="unexpected"):
        import_voice_pack(unexpected, profile_store=target_profiles, artifact_store=target_artifacts)
