from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from .models import MODEL_SPECS, PARALINGUISTIC_TAGS

AUTO_MODEL = "Auto"

# Product-facing names deliberately describe the job, not the implementation.
MODEL_UI_NAMES: dict[str, str] = {
    "multilingual-v3": "Multilingual",
    "turbo": "Expressive",
    "nano": "Light",
}
MODEL_UI_DESCRIPTIONS: dict[str, str] = {
    "multilingual-v3": "Best for Arabic and other supported languages.",
    "turbo": "Fast English speech with expressive tags such as laughs and sighs.",
    "nano": "The lightest option and usually the best fit for CPU-only computers.",
}
MODEL_SELECTIONS: tuple[str, ...] = (AUTO_MODEL, *MODEL_UI_NAMES.values())

QUALITY_MODES: tuple[str, ...] = ("Fast", "Balanced", "Best")
COMPUTE_CHOICES: tuple[str, ...] = ("Auto", "GPU", "CPU")


@dataclass(frozen=True)
class ProductSystemProfile:
    compute: str = "cpu"
    ram_gb: float | None = None
    vram_gb: float | None = None


def model_id_from_ui_name(value: str | None) -> str | None:
    if not value or value == AUTO_MODEL:
        return None
    for model_id, display in MODEL_UI_NAMES.items():
        if value == display:
            return model_id
    # Accept technical names too so saved pre-v1.1 settings remain usable.
    for model_id, spec in MODEL_SPECS.items():
        if value in {model_id, spec.name, spec.short_name}:
            return model_id
    return None


def model_ui_name(model_id: str) -> str:
    return MODEL_UI_NAMES.get(model_id, MODEL_SPECS[model_id].short_name)


def model_detail(model_id: str) -> str:
    spec = MODEL_SPECS[model_id]
    friendly = model_ui_name(model_id)
    description = MODEL_UI_DESCRIPTIONS[model_id]
    languages = f"{len(spec.languages)} languages" if spec.capabilities.multilingual else "English"
    return f"**{friendly}** · {languages}  \n{description}"


def contains_expression_tag(text: str) -> bool:
    lowered = (text or "").lower()
    return any(tag.lower() in lowered for tag in PARALINGUISTIC_TAGS)


def _in_ranges(char: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    value = ord(char)
    return any(start <= value <= end for start, end in ranges)


# Auto should be helpful without pretending to be certain. Distinctive scripts are
# detected directly. For Latin-script text we only select a language when several
# common words agree and one language clearly wins; otherwise Auto stays English.
_LATIN_HINTS: dict[str, frozenset[str]] = {
    "Spanish": frozenset({"hola", "esto", "esta", "una", "para", "pero", "como", "gracias", "prueba", "voz", "quiero", "puede"}),
    "French": frozenset({"bonjour", "merci", "avec", "pour", "mais", "une", "voix", "essai", "ceci", "dans", "vous", "être"}),
    "German": frozenset({"hallo", "danke", "und", "ist", "eine", "stimme", "test", "mit", "für", "nicht", "ich", "das"}),
    "Italian": frozenset({"ciao", "grazie", "questo", "questa", "una", "voce", "prova", "con", "per", "non", "sono", "che"}),
    "Portuguese": frozenset({"olá", "obrigado", "obrigada", "isto", "uma", "voz", "teste", "com", "para", "não", "você", "que"}),
    "Dutch": frozenset({"hallo", "dank", "dit", "een", "stem", "test", "met", "voor", "niet", "het", "dat", "van"}),
    "Turkish": frozenset({"merhaba", "teşekkür", "bu", "bir", "ses", "test", "için", "ile", "değil", "ben", "çok", "ve"}),
    "Swedish": frozenset({"hej", "tack", "detta", "en", "röst", "test", "med", "för", "inte", "och", "jag", "är"}),
    "Norwegian": frozenset({"hei", "takk", "dette", "en", "stemme", "test", "med", "for", "ikke", "og", "jeg", "er"}),
    "Danish": frozenset({"hej", "tak", "dette", "en", "stemme", "test", "med", "for", "ikke", "og", "jeg", "er"}),
    "Finnish": frozenset({"hei", "kiitos", "tämä", "ääni", "testi", "kanssa", "varten", "ei", "ja", "minä", "on", "että"}),
    "Polish": frozenset({"cześć", "dzień", "dziękuję", "to", "jest", "głos", "test", "dla", "nie", "i", "ja", "że"}),
    "Swahili": frozenset({"jambo", "asante", "hii", "sauti", "jaribio", "kwa", "na", "sio", "mimi", "ni", "una", "ya"}),
    "Malay": frozenset({"hai", "terima", "kasih", "ini", "suara", "ujian", "untuk", "dengan", "tidak", "saya", "dan", "adalah"}),
}

_LATIN_DIACRITIC_HINTS: dict[str, frozenset[str]] = {
    "Spanish": frozenset("ñ¿¡"),
    "French": frozenset("àâçéèêëîïôùûüÿœ"),
    "German": frozenset("äöüß"),
    "Portuguese": frozenset("ãõáâàçéêíóôú"),
    "Turkish": frozenset("ğışçöü"),
    "Swedish": frozenset("åäö"),
    "Norwegian": frozenset("æøå"),
    "Danish": frozenset("æøå"),
    "Finnish": frozenset("äö"),
    "Polish": frozenset("ąćęłńóśźż"),
}


def _latin_tokens(text: str) -> list[str]:
    lowered = unicodedata.normalize("NFC", text or "").lower()
    return re.findall(r"[^\W\d_]+", lowered, flags=re.UNICODE)


def detect_latin_language(text: str) -> str | None:
    """Return a conservative Latin-script language hint, or None when unsure."""
    tokens = _latin_tokens(text)
    if len(tokens) < 3:
        return None
    token_set = set(tokens)
    lowered = (text or "").lower()
    scores: dict[str, int] = {}
    for language, words in _LATIN_HINTS.items():
        word_score = len(token_set & words)
        diacritic_score = 1 if any(char in lowered for char in _LATIN_DIACRITIC_HINTS.get(language, ())) else 0
        scores[language] = word_score + diacritic_score
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] < 2:
        return None
    if len(ranked) > 1 and ranked[0][1] <= ranked[1][1]:
        return None
    return ranked[0][0]


def detect_script_language(text: str) -> str | None:
    """Detect supported languages conservatively without a heavy language model."""
    letters = [char for char in (text or "") if char.isalpha()]
    if not letters:
        return None

    # Check Japanese/Korean before Han so mixed Japanese text is not called Chinese.
    groups: tuple[tuple[str, tuple[tuple[int, int], ...], float], ...] = (
        ("Japanese", ((0x3040, 0x30FF), (0x31F0, 0x31FF)), 0.08),
        ("Korean", ((0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7AF)), 0.20),
        ("Arabic", ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF)), 0.20),
        ("Hebrew", ((0x0590, 0x05FF),), 0.20),
        ("Hindi", ((0x0900, 0x097F),), 0.20),
        ("Greek", ((0x0370, 0x03FF), (0x1F00, 0x1FFF)), 0.20),
        ("Russian", ((0x0400, 0x052F),), 0.20),
        ("Chinese", ((0x3400, 0x4DBF), (0x4E00, 0x9FFF)), 0.20),
    )
    for language, ranges, threshold in groups:
        matches = sum(1 for char in letters if _in_ranges(char, ranges))
        if matches / len(letters) >= threshold:
            return language

    return detect_latin_language(text)


def script_looks_arabic(text: str) -> bool:
    """Backward-compatible helper retained for tests/extensions."""
    return detect_script_language(text) == "Arabic"


def resolve_language(language_ui: str | None, script: str) -> str:
    if language_ui and language_ui != "Auto":
        return language_ui
    return detect_script_language(script) or "English"


def resolve_model_id(
    model_ui: str | None,
    language_ui: str | None,
    script: str,
    profile: ProductSystemProfile,
) -> str:
    explicit = model_id_from_ui_name(model_ui)
    language = resolve_language(language_ui, script)
    if explicit:
        if language != "English" and not MODEL_SPECS[explicit].capabilities.multilingual:
            raise ValueError(f"{model_ui} supports English only. Use Multilingual for {language}.")
        return explicit

    if language != "English":
        return "multilingual-v3"
    if contains_expression_tag(script):
        return "turbo"
    if profile.compute == "cpu":
        return "nano"
    if profile.vram_gb is not None and profile.vram_gb < 6:
        return "nano"
    return "turbo"


def compatible_models(language_ui: str | None, script: str) -> tuple[str, ...]:
    language = resolve_language(language_ui, script)
    if language == "English":
        return tuple(MODEL_SPECS)
    return tuple(model_id for model_id, spec in MODEL_SPECS.items() if spec.capabilities.multilingual)


def quality_policy(mode: str | None) -> dict[str, int | bool]:
    """Turn three human choices into reliability defaults.

    STT verification intentionally remains independent. It is expensive and belongs
    in Tools/Expert, not in the default generation path.
    """
    normalized = mode if mode in QUALITY_MODES else "Balanced"
    if normalized == "Fast":
        return {"quality_check": False, "auto_retries": 0, "best_of_n": 1}
    if normalized == "Best":
        return {"quality_check": True, "auto_retries": 0, "best_of_n": 2}
    return {"quality_check": True, "auto_retries": 0, "best_of_n": 1}


def safe_compare_order(model_ids: Iterable[str]) -> tuple[str, ...]:
    """Keep comparisons predictable and memory-safe by running one model at a time."""
    preferred = ("multilingual-v3", "turbo", "nano")
    requested = set(model_ids)
    return tuple(model_id for model_id in preferred if model_id in requested)


def human_model_status(installed: bool, loaded: bool, update_available: bool | None) -> str:
    if loaded:
        return "Ready · in memory"
    if not installed:
        return "Not installed"
    if update_available:
        return "Update available"
    return "Ready"
