from __future__ import annotations

import json

from studio.protocol import ArtifactRef, EngineBinding, VoiceSourceKind
from studio.voice_profile_store import (
    PROFILE_SCHEMA_VERSION,
    EngineVoiceBinding,
    VoiceProfileStore,
)


def test_profile_store_keeps_voice_identity_engine_independent(tmp_path):
    store = VoiceProfileStore(tmp_path / "profiles")
    reference = ArtifactRef(
        artifact_id="art_ref_1",
        mime_type="audio/wav",
        uri="local://artifacts/art_ref_1",
        sha256="abc123",
    )
    saved = store.create(
        "Narrator Main",
        "Narrator",
        source_kind=VoiceSourceKind.CLONE,
        reference=reference,
        supported_languages=("en", "ar"),
    )

    assert saved.profile.profile_id == "narrator-main"
    assert saved.profile.consistency_locked is True
    assert saved.profile.source.reference == reference
    assert saved.bindings == ()

    reloaded = store.get("narrator-main")
    assert reloaded is not None
    assert reloaded.profile.source.reference is not None
    assert reloaded.profile.source.reference.uri == "local://artifacts/art_ref_1"


def test_unicode_profile_ids_do_not_collapse_to_generic_voice(tmp_path):
    store = VoiceProfileStore(tmp_path / "profiles")
    arabic = store.create("صوت عربي", "صوت عربي", source_kind=VoiceSourceKind.READY, source_voice_id="preset-ar")
    japanese = store.create("ナレーター", "ナレーター", source_kind=VoiceSourceKind.READY, source_voice_id="preset-ja")

    assert arabic.profile.profile_id.startswith("صوت")
    assert japanese.profile.profile_id.startswith("ナレーター")
    assert arabic.profile.profile_id != japanese.profile.profile_id
    assert store.get(arabic.profile.profile_id) is not None
    assert store.get(japanese.profile.profile_id) is not None


def test_engine_binding_records_golden_recipe_without_changing_profile_revision(tmp_path):
    store = VoiceProfileStore(tmp_path / "profiles")
    store.create("voice-1", "Creator", source_kind=VoiceSourceKind.SAVED, source_voice_id="legacy-ref")

    result = store.add_binding(
        "voice-1",
        EngineVoiceBinding(
            engine_id="chatterbox-v3",
            model_id="multilingual-v3",
            model_revision="abc123",
            recipe_id="creator-golden",
            recipe_revision=4,
            style_recipes={"warm": "warm-v2", "creator": "creator-v4"},
            certified_languages=("en", "ar"),
            quality_score=92.5,
        ),
    )

    assert result.profile.revision == 1
    binding = result.binding_for("chatterbox-v3")
    assert binding is not None
    assert binding.recipe_id == "creator-golden"
    assert binding.style_recipes["warm"] == "warm-v2"

    raw = json.loads(store.path_for("voice-1").read_text(encoding="utf-8"))
    assert raw["schema_version"] == PROFILE_SCHEMA_VERSION
    assert "bindings" not in raw
    assert raw["profile"]["engine_bindings"][0]["engine_id"] == "chatterbox-v3"


def test_promoting_new_engine_binding_creates_new_voice_revision(tmp_path):
    store = VoiceProfileStore(tmp_path / "profiles")
    store.create("voice-1", "Creator", source_kind=VoiceSourceKind.SAVED, source_voice_id="legacy-ref")

    promoted = store.add_binding(
        "voice-1",
        EngineVoiceBinding(
            engine_id="qwen3-tts",
            model_id="base-1.7b",
            model_revision="q1",
            recipe_id="creator-qwen-v1",
            speaker_similarity_score=0.93,
        ),
        promote_revision=True,
    )

    assert promoted.profile.revision == 2
    assert promoted.profile.consistency_locked is True
    assert promoted.binding_for("qwen3-tts") is not None


def test_pronunciation_dictionary_is_profile_scoped(tmp_path):
    store = VoiceProfileStore(tmp_path / "profiles")
    store.create("voice-1", "Creator", source_kind=VoiceSourceKind.READY, source_voice_id="preset-1")
    updated = store.set_pronunciation_hint("voice-1", "Qwen", "Q-wen")
    assert updated.pronunciation_hints == {"Qwen": "Q-wen"}
    assert updated.profile.pronunciation_hints == {"Qwen": "Q-wen"}

    reloaded = store.get("voice-1")
    assert reloaded is not None
    assert reloaded.pronunciation_hints["Qwen"] == "Q-wen"


def test_v1_profile_migrates_to_v2_with_backup_and_is_idempotent(tmp_path):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    path = profiles / "voice-1.json"
    v1 = {
        "schema_version": 1,
        "profile": {
            "profile_id": "voice-1",
            "display_name": "Creator",
            "source": {"kind": "saved", "voice_id": "legacy-ref", "reference": None, "description": None, "schema_version": 1},
            "revision": 1,
            "default_style": "creator",
            "consistency_locked": True,
            "engine_bindings": [],
            "supported_languages": ["en"],
            "metadata": {},
            "schema_version": 1,
        },
        "bindings": [
            {
                "engine_id": "chatterbox-v3",
                "model_id": "multilingual-v3",
                "model_revision": "abc",
                "recipe_id": "golden",
                "recipe_revision": 2,
                "style_recipes": {"creator": "creator-v2"},
                "engine_voice_id": None,
                "prompt_artifact": None,
                "certified_languages": ["en"],
                "quality_score": 90.0,
                "speaker_similarity_score": None,
                "enabled": True,
                "metadata": {},
                "schema_version": 1,
            }
        ],
        "pronunciation_hints": {"Qwen": "Q-wen"},
        "preferred_styles": ["natural", "creator"],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    path.write_text(json.dumps(v1), encoding="utf-8")

    store = VoiceProfileStore(profiles)
    migrated = store.get("voice-1")
    assert migrated is not None
    assert migrated.binding_for("chatterbox-v3") is not None
    assert migrated.profile.pronunciation_hints["Qwen"] == "Q-wen"

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == PROFILE_SCHEMA_VERSION
    assert "bindings" not in raw
    backup = tmp_path / "backups" / "voice-profiles-v1" / "voice-1.json"
    assert backup.exists()
    assert json.loads(backup.read_text(encoding="utf-8"))["schema_version"] == 1

    before = path.read_text(encoding="utf-8")
    assert store.migrate_all() == 0
    assert path.read_text(encoding="utf-8") == before


def test_profile_files_do_not_need_account_identity_or_absolute_paths(tmp_path):
    store = VoiceProfileStore(tmp_path / "profiles")
    store.create("voice-1", "Narrator", source_kind=VoiceSourceKind.READY, source_voice_id="preset-1")
    raw = store.path_for("voice-1").read_text(encoding="utf-8")
    assert "account" not in raw.lower()
    assert "/home/" not in raw
    assert "C:\\" not in raw


def test_engine_voice_binding_alias_points_to_public_canonical_contract():
    assert EngineVoiceBinding is EngineBinding
