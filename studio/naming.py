from __future__ import annotations

import re
import unicodedata


_WINDOWS_DEVICE = re.compile(r"^(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])$", re.IGNORECASE)


def safe_local_name(value: str | None, *, fallback: str = "item", casefold: bool = False) -> str:
    """Return a Unicode-preserving cross-platform-safe local stem/identifier.

    The helper removes path separators/control punctuation, normalizes Unicode
    compatibility forms, avoids Windows reserved device names, and never returns an
    empty/dot-only path component. It intentionally does not transliterate non-Latin
    names: Arabic/CJK voice names remain recognizable and collision-resistant.
    """

    normalized = unicodedata.normalize("NFKC", (value or fallback).strip())
    clean = re.sub(r"[^\w.-]+", "-", normalized, flags=re.UNICODE).strip(" .-_")
    if not clean or clean in {".", ".."}:
        clean = fallback
    if _WINDOWS_DEVICE.fullmatch(clean):
        clean = f"_{clean}"
    return clean.casefold() if casefold else clean
