from __future__ import annotations

from studio.protocol import ArtifactRef, VoiceSourceKind
from studio.voice_profile_store import EngineVoiceBinding, VoiceProfileStore


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

    reloaded = store.get("voice-1")
    assert reloaded is not None
    assert reloaded.pronunciation_hints["Qwen"] == "Q-wen"


def test_profile_files_do_not_need_account_identity_or_absolute_paths(tmp_path):
    store = VoiceProfileStore(tmp_path / "profiles")
    store.create("voice-1", "Narrator", source_kind=VoiceSourceKind.READY, source_voice_id="preset-1")
    raw = store.path_for("voice-1").read_text(encoding="utf-8")
    assert "account" not in raw.lower()
    assert "/home/" not in raw
    assert "C:\\" not in raw
