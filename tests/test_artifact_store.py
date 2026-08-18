from __future__ import annotations

from pathlib import Path

import pytest

from studio.artifact_store import ArtifactStore
from studio.protocol import ArtifactRef


def test_register_file_hides_absolute_source_path(tmp_path):
    source = tmp_path / "private" / "voice.wav"
    source.parent.mkdir()
    source.write_bytes(b"RIFF-demo-audio")
    store = ArtifactStore(tmp_path / "app-data" / "artifacts")

    ref = store.register_file(source, artifact_id="voice-reference", mime_type="audio/wav")

    assert ref.uri == "local://artifacts/voice-reference"
    assert str(tmp_path) not in ref.uri
    assert ref.size_bytes == len(b"RIFF-demo-audio")
    assert ref.sha256
    assert store.resolve(ref).read_bytes() == source.read_bytes()


def test_deterministic_registration_is_idempotent_but_never_overwrites_other_content(tmp_path):
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    first.write_bytes(b"same")
    second.write_bytes(b"different")
    store = ArtifactStore(tmp_path / "artifacts")

    a = store.register_file(first, artifact_id="voice-reference")
    b = store.register_file(first, artifact_id="voice-reference")
    assert a.sha256 == b.sha256

    with pytest.raises(FileExistsError):
        store.register_file(second, artifact_id="voice-reference")
    assert store.resolve(a).read_bytes() == b"same"


def test_same_logical_id_cannot_be_ambiguous_across_extensions(tmp_path):
    wav = tmp_path / "clip.wav"
    mp3 = tmp_path / "clip.mp3"
    wav.write_bytes(b"same-bytes")
    mp3.write_bytes(b"same-bytes")
    store = ArtifactStore(tmp_path / "artifacts")

    ref = store.register_file(wav, artifact_id="clip")
    with pytest.raises(FileExistsError, match="another stored file"):
        store.register_file(mp3, artifact_id="clip")
    assert store.resolve(ref).suffix == ".wav"


def test_unicode_artifact_ids_remain_distinct_and_windows_device_names_are_avoided(tmp_path):
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    store = ArtifactStore(tmp_path / "artifacts")

    ar = store.register_file(first, artifact_id="صوت-مرجعي")
    ja = store.register_file(second, artifact_id="音声-参照")
    assert ar.artifact_id != ja.artifact_id
    assert store.resolve(ar).read_bytes() == b"same"
    assert store.resolve(ja).read_bytes() == b"same"

    device = store.register_file(first, artifact_id="CON")
    assert device.artifact_id.casefold() != "con"
    assert store.resolve(device).is_file()


def test_resolve_detects_tampered_artifact_when_hash_is_known(tmp_path):
    source = tmp_path / "voice.wav"
    source.write_bytes(b"good")
    store = ArtifactStore(tmp_path / "artifacts")
    ref = store.register_file(source, artifact_id="voice")
    stored = store.resolve(ref)
    stored.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="integrity"):
        store.resolve(ref)


def test_resolve_rejects_preexisting_ambiguous_store_state(tmp_path):
    directory = tmp_path / "artifacts"
    directory.mkdir()
    (directory / "clip.wav").write_bytes(b"wav")
    (directory / "clip.mp3").write_bytes(b"mp3")
    store = ArtifactStore(directory)

    with pytest.raises(ValueError, match="ambiguous"):
        store.resolve(ArtifactRef("clip", "audio/wav", "local://artifacts/clip"))


def test_non_copy_registration_cannot_point_outside_store(tmp_path):
    source = tmp_path / "outside.wav"
    source.write_bytes(b"audio")
    store = ArtifactStore(tmp_path / "artifacts")

    with pytest.raises(ValueError):
        store.register_file(source, copy=False)


def test_resolver_rejects_non_local_traversal_alias_and_mismatched_identity(tmp_path):
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    store = ArtifactStore(tmp_path / "artifacts")
    ref = store.register_file(source, artifact_id="clip")

    with pytest.raises(ValueError):
        store.resolve(ArtifactRef("a", "audio/wav", "file:///private/voice.wav"))
    with pytest.raises(ValueError):
        store.resolve(ArtifactRef("a", "audio/wav", "local://artifacts/../secret"))
    with pytest.raises(ValueError, match="canonical"):
        store.resolve(ArtifactRef("different", ref.mime_type, ref.uri, sha256=ref.sha256))
    with pytest.raises(ValueError, match="canonical"):
        store.resolve(ArtifactRef("clip", ref.mime_type, "local://artifacts/CLIP", sha256=ref.sha256))


def test_remove_is_scoped_to_store(tmp_path):
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    store = ArtifactStore(tmp_path / "artifacts")
    ref = store.register_file(source, artifact_id="clip")
    stored = store.resolve(ref)
    assert stored.exists()
    assert store.remove(ref) is True
    assert not stored.exists()
    assert source.exists()
