from __future__ import annotations

from studio.model_profiles import capabilities_for, language_control_needed, profile_for
from studio.recipes import RecipeStore


def test_multilingual_creator_recipe_preserves_existing_sound():
    recipe = profile_for("multilingual-v3", "Creator")
    assert recipe.exaggeration == 0.65
    assert recipe.cfg_weight == 0.30
    assert recipe.temperature == 0.80
    assert recipe.repetition_penalty == 1.20
    assert recipe.min_p == 0.05
    assert recipe.top_p == 1.00
    assert recipe.top_k == 1000
    assert recipe.speech_speed == 1.00


def test_turbo_and_nano_use_their_own_sampling_profiles():
    turbo = profile_for("turbo", "Creator")
    nano = profile_for("nano", "Creator")
    multilingual = profile_for("multilingual-v3", "Creator")
    assert turbo.top_p == 0.95
    assert turbo.top_k == 1000
    assert nano.top_p == 0.92
    assert nano.top_k == 800
    assert (turbo.top_p, turbo.top_k) != (nano.top_p, nano.top_k)
    assert (turbo.top_p, turbo.top_k) != (multilingual.top_p, multilingual.top_k)


def test_capabilities_hide_controls_the_model_does_not_use():
    multilingual = capabilities_for("multilingual-v3")
    turbo = capabilities_for("turbo")
    nano = capabilities_for("nano")
    assert multilingual.multilingual is True
    assert multilingual.exaggeration is True
    assert multilingual.cfg_weight is True
    assert multilingual.min_p is True
    assert turbo.multilingual is False
    assert turbo.expressive_tags is True
    assert turbo.exaggeration is False
    assert turbo.cfg_weight is False
    assert turbo.min_p is False
    assert nano.multilingual is False
    assert nano.top_k is True


def test_language_control_is_only_needed_for_auto_or_multilingual():
    assert language_control_needed(None) is True
    assert language_control_needed("multilingual-v3") is True
    assert language_control_needed("turbo") is False
    assert language_control_needed("nano") is False


def test_recipe_store_round_trip_and_delete(tmp_path):
    store = RecipeStore(tmp_path / "recipes.json")
    saved = store.save(
        name="My favorite",
        voice="young_native",
        model_id="multilingual-v3",
        language="English",
        style="Creator",
        speech_speed=0.97,
        seed=424242,
        generation={
            "exaggeration": 0.65,
            "cfg_weight": 0.3,
            "temperature": 0.81,
            "repetition_penalty": 1.25,
            "min_p": 0.04,
            "top_p": 0.96,
            "top_k": 900,
        },
        finishing={"trim_silence": True, "peak_normalize": False, "fade_ms": 35},
    )
    loaded = store.get(saved.id)
    assert loaded is not None
    assert loaded.name == "My favorite"
    assert loaded.voice == "young_native"
    assert loaded.model_id == "multilingual-v3"
    assert loaded.language == "English"
    assert loaded.style == "Creator"
    assert loaded.speech_speed == 0.97
    assert loaded.seed == 424242
    assert loaded.generation == {
        "exaggeration": 0.65,
        "cfg_weight": 0.3,
        "temperature": 0.81,
        "repetition_penalty": 1.25,
        "min_p": 0.04,
        "top_p": 0.96,
        "top_k": 900,
    }
    assert loaded.finishing == {"trim_silence": True, "peak_normalize": False, "fade_ms": 35}
    assert store.delete(saved.id) is True
    assert store.get(saved.id) is None
