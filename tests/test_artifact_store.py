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


def test_resolve_detects_tampered_artifact_when_hash_is_known(tmp_path):
    source = tmp_path / "voice.wav"
    source.write_bytes(b"good")
    store = ArtifactStore(tmp_path / "artifacts")
    ref = store.register_file(source, artifact_id="voice")
    stored = store.resolve(ref)
    stored.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="integrity"):
        store.resolve(ref)


def test_non_copy_registration_cannot_point_outside_store(tmp_path):
    source = tmp_path / "outside.wav"
    source.write_bytes(b"audio")
    store = ArtifactStore(tmp_path / "artifacts")

    with pytest.raises(ValueError):
        store.register_file(source, copy=False)


def test_resolver_rejects_non_local_and_traversal_refs(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ValueError):
        store.resolve(ArtifactRef("a", "audio/wav", "file:///private/voice.wav"))
    with pytest.raises(ValueError):
        store.resolve(ArtifactRef("a", "audio/wav", "local://artifacts/../secret"))


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
