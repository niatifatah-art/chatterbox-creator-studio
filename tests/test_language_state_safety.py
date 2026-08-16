from __future__ import annotations

from studio.product import resolve_language


def test_stale_hidden_english_state_recovers_arabic_generation_language():
    text = "مرحبا، هذا اختبار واضح للصوت باللغة العربية."
    assert resolve_language("English", text) == "Arabic"


def test_stale_hidden_english_state_recovers_detected_spanish_generation_language():
    text = "Hola, esto es una prueba de voz para mi proyecto."
    assert resolve_language("English", text) == "Spanish"


def test_explicit_non_english_choice_is_still_respected_when_script_is_ambiguous():
    assert resolve_language("French", "Studio test 123") == "French"
