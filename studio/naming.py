from __future__ import annotations

import hashlib
import re
import unicodedata


# Windows treats these device names as reserved even when followed by an extension,
# e.g. NUL.txt or COM1.log. Match the reserved base and any suffix after a period.
_WINDOWS_DEVICE = re.compile(
    r"^(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?$",
    re.IGNORECASE,
)
_DEFAULT_MAX_UTF8_BYTES = 180


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    digest = hashlib.sha256(encoded).hexdigest()[:12]
    suffix = f"-{digest}"
    budget = max(1, max_bytes - len(suffix.encode("utf-8")))
    kept: list[str] = []
    used = 0
    for char in value:
        width = len(char.encode("utf-8"))
        if used + width > budget:
            break
        kept.append(char)
        used += width
    prefix = "".join(kept).rstrip(" .-_") or "item"
    return f"{prefix}{suffix}"


def safe_local_name(
    value: str | None,
    *,
    fallback: str = "item",
    casefold: bool = False,
    max_utf8_bytes: int = _DEFAULT_MAX_UTF8_BYTES,
) -> str:
    """Return a Unicode-preserving conservative cross-platform-safe local stem.

    The helper removes separators/control punctuation, normalizes Unicode compatibility
    forms, avoids Windows reserved device names, and keeps components comfortably below
    common filesystem component limits. Long Unicode names receive a deterministic hash
    suffix instead of being truncated into collisions.

    It intentionally does not transliterate non-Latin names: Arabic/CJK voice names
    remain recognizable and collision-resistant.
    """

    if max_utf8_bytes < 32:
        raise ValueError("max_utf8_bytes must leave room for a readable name and collision suffix.")
    normalized = unicodedata.normalize("NFKC", (value or fallback).strip())
    clean = re.sub(r"[^\w.-]+", "-", normalized, flags=re.UNICODE).strip(" .-_")
    if not clean or clean in {".", ".."}:
        clean = re.sub(r"[^\w.-]+", "-", unicodedata.normalize("NFKC", fallback), flags=re.UNICODE).strip(" .-_") or "item"
    if casefold:
        clean = clean.casefold()
    if _WINDOWS_DEVICE.fullmatch(clean):
        clean = f"_{clean}"
    clean = _truncate_utf8(clean, max_utf8_bytes)
    # Truncation never creates a reserved exact device name because it adds a hash,
    # but keep the invariant explicit if this helper changes later.
    if _WINDOWS_DEVICE.fullmatch(clean):
        clean = f"_{clean}"
    return clean
