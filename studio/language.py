from __future__ import annotations

import re
import unicodedata


# Lightweight language hints shared by the creator UI and Speech Core. This is not a
# claim of perfect language identification: distinctive scripts are detected directly,
# while Latin-script languages require several common-word/diacritic signals. Callers
# may always provide an explicit language override.
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

LANGUAGE_NAME_TO_CODE: dict[str, str] = {
    "Arabic": "ar",
    "Danish": "da",
    "German": "de",
    "Greek": "el",
    "English": "en",
    "Spanish": "es",
    "Finnish": "fi",
    "French": "fr",
    "Hebrew": "he",
    "Hindi": "hi",
    "Italian": "it",
    "Japanese": "ja",
    "Korean": "ko",
    "Malay": "ms",
    "Dutch": "nl",
    "Norwegian": "no",
    "Polish": "pl",
    "Portuguese": "pt",
    "Russian": "ru",
    "Swedish": "sv",
    "Swahili": "sw",
    "Turkish": "tr",
    "Chinese": "zh",
}
LANGUAGE_CODE_TO_NAME = {code: name for name, code in LANGUAGE_NAME_TO_CODE.items()}


def _in_ranges(char: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    value = ord(char)
    return any(start <= value <= end for start, end in ranges)


def _latin_tokens(text: str) -> list[str]:
    lowered = unicodedata.normalize("NFC", text or "").lower()
    return re.findall(r"[^\W\d_]+", lowered, flags=re.UNICODE)


def detect_latin_language(text: str) -> str | None:
    """Return a conservative supported Latin-script language name, or None."""

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
    """Detect a supported language conservatively without a heavy language model."""

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


def detect_language_code(text: str, *, fallback: str = "en") -> str:
    """Return a supported language code, falling back when detection is uncertain."""

    name = detect_script_language(text)
    if name is None:
        return fallback
    return LANGUAGE_NAME_TO_CODE.get(name, fallback)


def normalize_language_code(value: str | None, text: str = "", *, fallback: str = "en") -> str:
    """Normalize user/API language input into a supported lower-case code.

    `auto` uses the shared lightweight detector. Human-facing language names are also
    accepted so the current Gradio UI can migrate without duplicating conversion logic.
    """

    raw = (value or "auto").strip()
    if not raw or raw.casefold() == "auto":
        return detect_language_code(text, fallback=fallback)
    if raw in LANGUAGE_NAME_TO_CODE:
        return LANGUAGE_NAME_TO_CODE[raw]
    code = raw.casefold().split("-", 1)[0]
    if code in LANGUAGE_CODE_TO_NAME:
        return code
    raise ValueError(f"Unsupported language '{value}'.")


def script_looks_arabic(text: str) -> bool:
    return detect_script_language(text) == "Arabic"
