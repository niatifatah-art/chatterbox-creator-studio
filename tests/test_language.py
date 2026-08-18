from __future__ import annotations

import pytest

from studio.language import (
    detect_language_code,
    detect_script_language,
    normalize_language_code,
)
from studio.product import resolve_language


def test_shared_language_detection_keeps_product_behavior_for_distinct_scripts():
    cases = {
        "مرحبا كيف حالك اليوم": ("Arabic", "ar"),
        "Привет это тест голоса": ("Russian", "ru"),
        "こんにちは、これは音声テストです": ("Japanese", "ja"),
        "안녕하세요 이것은 음성 테스트입니다": ("Korean", "ko"),
        "你好，这是一个语音测试": ("Chinese", "zh"),
    }
    for text, (name, code) in cases.items():
        assert detect_script_language(text) == name
        assert detect_language_code(text) == code
        assert resolve_language("Auto", text) == name


def test_latin_detection_stays_conservative_and_explicit_override_wins():
    assert detect_script_language("bonjour merci avec une voix pour ce test") == "French"
    assert detect_script_language("hello tiny") is None
    assert normalize_language_code("fr", "anything") == "fr"
    assert normalize_language_code("French", "anything") == "fr"


def test_auto_language_falls_back_to_english_when_uncertain():
    assert normalize_language_code("auto", "hello tiny") == "en"
    assert normalize_language_code(None, "12345") == "en"


def test_unknown_explicit_language_is_rejected_not_silently_changed():
    with pytest.raises(ValueError, match="Unsupported language"):
        normalize_language_code("xx", "hello")
