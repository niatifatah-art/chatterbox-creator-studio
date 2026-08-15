from __future__ import annotations

import re

DEFAULT_MAX_CHARS = 280
DEFAULT_MIN_CHARS = 24

# Latin-style punctuation normally has whitespace after it. CJK/Arabic question
# punctuation frequently does not, so those boundaries also split without spaces.
_BOUNDARY_RE = re.compile(r"(?:(?<=[.!?])\s+|(?<=[。！？؟])\s*)")
_CJK_ENDERS = {"。", "！", "？"}


def normalize_whitespace(text: str) -> str:
    return " ".join((text or "").split())


def split_sentences(text: str) -> list[str]:
    text = normalize_whitespace(text)
    if not text:
        return []
    return [part.strip() for part in _BOUNDARY_RE.split(text) if part.strip()]


def _join_parts(left: str, right: str) -> str:
    if not left:
        return right
    if left[-1] in _CJK_ENDERS:
        return f"{left}{right}"
    return f"{left} {right}"


def _word_chunks(text: str, max_chars: int) -> list[str]:
    words = text.split()
    if len(words) <= 1:
        return [text[index : index + max_chars] for index in range(0, len(text), max_chars)]

    chunks: list[str] = []
    current = ""
    for word in words:
        candidate = _join_parts(current, word)
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def smart_chunks(
    text: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    min_chars: int = DEFAULT_MIN_CHARS,
) -> list[str]:
    """Split creator text into conservative multilingual TTS chunks.

    The function prefers sentence boundaries, understands common Arabic/CJK
    sentence punctuation, combines short sentences where possible, and avoids
    leaving a tiny final fragment when it can safely be merged back.
    """
    if max_chars < 32:
        raise ValueError("max_chars must be at least 32.")
    if min_chars < 0:
        raise ValueError("min_chars cannot be negative.")

    text = normalize_whitespace(text)
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    pieces: list[str] = []
    for sentence in split_sentences(text):
        if len(sentence) <= max_chars:
            pieces.append(sentence)
        else:
            pieces.extend(_word_chunks(sentence, max_chars))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = _join_parts(current, piece)
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = piece
        else:
            current = candidate
    if current:
        chunks.append(current)

    if len(chunks) > 1 and len(chunks[-1]) < min_chars:
        candidate = _join_parts(chunks[-2], chunks[-1])
        # A small overflow is better than sending a tiny unstable fragment.
        if len(candidate) <= max_chars + min_chars:
            chunks[-2:] = [candidate]

    return chunks
