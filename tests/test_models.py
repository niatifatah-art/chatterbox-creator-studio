from studio.models import MODEL_SPECS, language_code_from_name, model_id_from_name


def test_registry_has_foundation_models():
    assert set(MODEL_SPECS) == {"multilingual-v3", "turbo", "nano"}


def test_turbo_and_nano_are_english_and_tag_capable():
    for model_id in ("turbo", "nano"):
        spec = MODEL_SPECS[model_id]
        assert spec.languages == ("en",)
        assert spec.capabilities.supports_paralinguistic_tags
        assert not spec.capabilities.supports_cfg


def test_multilingual_v3_supports_arabic_and_russian():
    spec = MODEL_SPECS["multilingual-v3"]
    assert "ar" in spec.languages
    assert "ru" in spec.languages
    assert spec.capabilities.supports_cfg


def test_display_name_helpers_are_stable():
    assert model_id_from_name("Chatterbox Turbo") == "turbo"
    assert language_code_from_name("Arabic") == "ar"
