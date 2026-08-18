from __future__ import annotations

import pytest

from studio.protocol import EngineBinding, VoiceSourceKind
from studio.voice_profile_store import VoiceProfileStore


def test_promoting_binding_pins_engine_and_increments_voice_revision(tmp_path):
    store = VoiceProfileStore(tmp_path / "profiles")
    store.create("voice", "Voice", source_kind=VoiceSourceKind.SAVED, source_voice_id="legacy")

    candidate = store.add_binding(
        "voice",
        EngineBinding(engine_id="chatterbox-v3", model_id="multilingual-v3", recipe_id="candidate"),
        promote_revision=False,
    )
    assert candidate.profile.preferred_engine_id is None
    assert candidate.profile.revision == 1

    promoted = store.add_binding(
        "voice",
        EngineBinding(engine_id="chatterbox-v3", model_id="multilingual-v3", recipe_id="golden"),
        promote_revision=True,
    )
    assert promoted.profile.preferred_engine_id == "chatterbox-v3"
    assert promoted.profile.revision == 2


def test_setting_preferred_engine_requires_existing_enabled_binding(tmp_path):
    store = VoiceProfileStore(tmp_path / "profiles")
    store.create("voice", "Voice", source_kind=VoiceSourceKind.SAVED, source_voice_id="legacy")
    with pytest.raises(ValueError, match="no enabled binding"):
        store.set_preferred_engine("voice", "chatterbox-v3")

    store.add_binding("voice", EngineBinding(engine_id="chatterbox-v3", model_id="multilingual-v3"))
    preferred = store.set_preferred_engine("voice", "chatterbox-v3")
    assert preferred.profile.preferred_engine_id == "chatterbox-v3"
    assert preferred.profile.revision == 2

    # Setting the already-current engine is idempotent and does not create revisions.
    unchanged = store.set_preferred_engine("voice", "chatterbox-v3")
    assert unchanged.profile.revision == 2
