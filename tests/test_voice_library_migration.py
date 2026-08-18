from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path

import pytest

from studio.voices import VoiceLibrary, VoiceReferenceAnalysis


def _write_wav(path: Path, seconds: float = 0.25, sample_rate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        for index in range(frames):
            sample = int(4500 * math.sin(2 * math.pi * 220 * index / sample_rate))
            handle.writeframesraw(struct.pack("<h", sample))


def test_legacy_voice_migrates_non_destructively_and_without_absolute_path_leak(tmp_path: Path):
    voices_dir = tmp_path / "data" / "voices"
    wav = voices_dir / "صوت-تجريبي.wav"
    _write_wav(wav)
    legacy_json = voices_dir / "صوت-تجريبي.json"
    legacy_json.write_text(
        json.dumps(
            {
                "name": "صوت-تجريبي",
                "path": str(wav.resolve()),
                "duration_seconds": 0.25,
                "sample_rate": 8000,
                "channels": 1,
                "warning": None,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    library = VoiceLibrary(voices_dir)
    assert "صوت-تجريبي" in library.list()
    assert wav.exists()  # migration never consumes/deletes the working legacy mirror

    records = library.profile_store.list()
    assert len(records) == 1
    record = records[0]
    assert record.profile.display_name == "صوت-تجريبي"
    assert record.profile.profile_id.startswith("صوت")
    assert record.profile.source.reference is not None
    assert record.profile.source.reference.uri.startswith("local://artifacts/")

    raw = library.profile_store.path_for(record.profile.profile_id).read_text(encoding="utf-8")
    assert str(tmp_path.resolve()) not in raw
    assert "/home/" not in raw
    assert "C:\\" not in raw

    backup_dir = tmp_path / "data" / "speech-core" / "backups" / "legacy-voices-v1"
    assert (backup_dir / wav.name).exists()
    assert (backup_dir / legacy_json.name).exists()

    # Starting the facade again is idempotent and does not create a second identity.
    second = VoiceLibrary(voices_dir)
    assert len(second.profile_store.list()) == 1
    assert second.profile_store.list()[0].profile.profile_id == record.profile.profile_id


def test_corrupt_legacy_metadata_falls_back_to_audio_analysis(tmp_path: Path):
    voices_dir = tmp_path / "data" / "voices"
    wav = voices_dir / "Narrator.wav"
    _write_wav(wav, seconds=0.4)
    (voices_dir / "Narrator.json").write_text("{bad json", encoding="utf-8")

    library = VoiceLibrary(voices_dir)
    profile = library.profile("Narrator")
    assert isinstance(profile, VoiceReferenceAnalysis)
    assert profile is not None
    assert profile.duration_seconds is not None
    assert 0.35 < profile.duration_seconds < 0.45

    record = library._record_for_name("Narrator")
    assert record is not None
    analysis = record.profile.metadata["reference_analysis"]
    assert analysis["duration_seconds"] == profile.duration_seconds
    assert "path" not in analysis


def test_rename_changes_display_name_but_keeps_stable_profile_id_and_old_id_alias(tmp_path: Path):
    voices_dir = tmp_path / "data" / "voices"
    source = tmp_path / "source.wav"
    _write_wav(source)
    library = VoiceLibrary(voices_dir)
    name, _ = library.save(str(source), "Original")
    before = library._record_for_name(name)
    assert before is not None
    profile_id = before.profile.profile_id

    renamed = library.rename(name, "New Name")
    after = library._record_for_name(renamed)
    assert renamed == "New-Name"
    assert after is not None
    assert after.profile.profile_id == profile_id
    assert after.profile.display_name == "New-Name"
    assert library.path_for(renamed) is not None

    # Legacy projects may still have stored the old name. Because the immutable
    # profile ID originated from that name, the facade keeps it as a compatibility
    # alias even though only the new display name appears in the picker.
    assert name not in library.list()
    assert library.path_for(name) is not None


def test_delete_through_old_alias_removes_current_mirror_without_resurrection(tmp_path: Path):
    voices_dir = tmp_path / "data" / "voices"
    source = tmp_path / "source.wav"
    _write_wav(source)
    library = VoiceLibrary(voices_dir)
    old_name, _ = library.save(str(source), "Original")
    new_name = library.rename(old_name, "Current Name")
    current_path = voices_dir / f"{new_name}.wav"
    assert current_path.exists()

    # An old project may still ask to delete using the pre-rename alias. Delete must
    # resolve the identity first and remove the *current* legacy mirror, otherwise it
    # would be re-imported on the next startup.
    assert library.delete(old_name)
    assert not current_path.exists()
    assert library._record_for_name(new_name) is None

    reopened = VoiceLibrary(voices_dir)
    assert new_name not in reopened.list()
    assert reopened._record_for_name(old_name) is None


def test_old_alias_is_reserved_so_new_save_cannot_hijack_existing_identity(tmp_path: Path):
    voices_dir = tmp_path / "data" / "voices"
    source = tmp_path / "source.wav"
    replacement = tmp_path / "replacement.wav"
    _write_wav(source)
    _write_wav(replacement, seconds=0.35)
    library = VoiceLibrary(voices_dir)
    old_name, _ = library.save(str(source), "Original")
    library.rename(old_name, "Current Name")

    with pytest.raises(FileExistsError, match="retained as an alias"):
        library.save(str(replacement), old_name)


def test_legacy_mirror_uses_windows_safe_names_without_destroying_unicode(tmp_path: Path):
    voices_dir = tmp_path / "data" / "voices"
    source = tmp_path / "source.wav"
    _write_wav(source)
    library = VoiceLibrary(voices_dir)

    reserved, reserved_path = library.save(str(source), "CON")
    assert reserved.casefold() != "con"
    assert reserved_path.is_file()

    arabic = library.duplicate(reserved, "صوت عربي")
    assert arabic.startswith("صوت")
    assert library.path_for(arabic) is not None


def test_duplicate_owns_an_independent_artifact_and_delete_preserves_original(tmp_path: Path):
    voices_dir = tmp_path / "data" / "voices"
    source = tmp_path / "source.wav"
    _write_wav(source)
    library = VoiceLibrary(voices_dir)
    original, _ = library.save(str(source), "Original")
    duplicate = library.duplicate(original)

    first = library._record_for_name(original)
    second = library._record_for_name(duplicate)
    assert first is not None and second is not None
    assert first.profile.source.reference is not None
    assert second.profile.source.reference is not None
    assert first.profile.source.reference.sha256 == second.profile.source.reference.sha256
    assert first.profile.source.reference.artifact_id != second.profile.source.reference.artifact_id

    original_artifact = library.artifact_store.resolve(first.profile.source.reference)
    assert library.delete(duplicate)
    assert original_artifact.exists()
    assert library.path_for(original) is not None
