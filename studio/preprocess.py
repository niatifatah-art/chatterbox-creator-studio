from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_NUMBER_RE = re.compile(r"(?<!\w)[+-]?(?:\d[\d,._]*\d|\d)(?!\w)")
_REPEAT_PUNCT_RE = re.compile(r"([!?.,])\1{2,}")
_SPACE_RE = re.compile(r"[ \t]+")


@dataclass(frozen=True)
class PreprocessOptions:
    normalize_unicode: bool = True
    normalize_punctuation: bool = False
    normalize_numbers: bool = False
    replace_urls: bool = False
    collapse_repeated_punctuation: bool = False
    normalize_whitespace: bool = True


@dataclass(frozen=True)
class ProcessedText:
    original: str
    processed: str
    changed: bool
    warnings: tuple[str, ...] = ()


def _normalize_punctuation(text: str) -> str:
    table = str.maketrans(
        {
            "“": '"',
            "”": '"',
            "„": '"',
            "‘": "'",
            "’": "'",
            "—": "-",
            "–": "-",
            "…": "...",
            "\u00a0": " ",
        }
    )
    return text.translate(table)


def _number_to_words(match: re.Match[str], language_id: str, warnings: list[str]) -> str:
    raw = match.group(0)
    cleaned = raw.replace(",", "").replace("_", "")
    try:
        value: int | float
        if "." in cleaned:
            value = float(cleaned)
        else:
            value = int(cleaned)
    except ValueError:
        return raw
    try:
        from num2words import num2words  # optional dependency

        return str(num2words(value, lang=language_id))
    except Exception:
        if "Number normalization requested but optional package 'num2words' is unavailable for this language." not in warnings:
            warnings.append(
                "Number normalization requested but optional package 'num2words' is unavailable for this language."
            )
        return raw


def process_text(
    text: str,
    language_id: str = "en",
    options: PreprocessOptions | None = None,
) -> ProcessedText:
    options = options or PreprocessOptions()
    original = text or ""
    processed = original
    warnings: list[str] = []

    if options.normalize_unicode:
        processed = unicodedata.normalize("NFC", processed)
    if options.normalize_punctuation:
        processed = _normalize_punctuation(processed)
    if options.replace_urls:
        processed = _URL_RE.sub(" URL ", processed)
    if options.collapse_repeated_punctuation:
        processed = _REPEAT_PUNCT_RE.sub(r"\1\1", processed)
    if options.normalize_numbers:
        processed = _NUMBER_RE.sub(lambda match: _number_to_words(match, language_id, warnings), processed)
    if options.normalize_whitespace:
        # Preserve line breaks because creators often use them as visual script structure.
        lines = [_SPACE_RE.sub(" ", line).strip() for line in processed.splitlines()]
        processed = "\n".join(lines).strip()

    return ProcessedText(
        original=original,
        processed=processed,
        changed=processed != original,
        warnings=tuple(warnings),
    )
