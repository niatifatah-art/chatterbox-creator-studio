from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Union

MAX_PAUSE_SECONDS = 30.0
_PAUSE_RE = re.compile(
    r"\[\s*pause\s*(?:=|:)\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ms|s)?\s*\]",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class Speech:
    text: str


@dataclass(frozen=True)
class Pause:
    seconds: float


Segment = Union[Speech, Pause]


def _to_seconds(value: str, unit: str | None) -> float:
    number = float(value)
    seconds = number / 1000.0 if (unit or "").lower() == "ms" else number
    if not 0 <= seconds <= MAX_PAUSE_SECONDS:
        raise ValueError(f"Pause must be between 0 and {MAX_PAUSE_SECONDS:g} seconds.")
    return seconds


def parse_script(script: str) -> list[Segment]:
    """Parse creator text into speech and deterministic-pause segments.

    Accepted syntax:
      [pause=0.35]
      [pause:1.2s]
      [pause=250ms]

    Plain punctuation is intentionally left to Chatterbox. Only explicit pause
    markers are converted to digital silence.
    """
    script = script or ""
    segments: list[Segment] = []
    cursor = 0

    for match in _PAUSE_RE.finditer(script):
        speech = script[cursor : match.start()].strip()
        if speech:
            segments.append(Speech(speech))
        segments.append(Pause(_to_seconds(match.group("value"), match.group("unit"))))
        cursor = match.end()

    tail = script[cursor:].strip()
    if tail:
        segments.append(Speech(tail))

    return _merge_adjacent_pauses(segments)


def find_invalid_pause_markers(script: str) -> list[str]:
    """Return pause-looking markers that do not match the supported syntax."""
    candidates = re.findall(r"\[[^\]]*pause[^\]]*\]", script or "", flags=re.IGNORECASE)
    return [candidate for candidate in candidates if _PAUSE_RE.fullmatch(candidate) is None]


def _merge_adjacent_pauses(segments: Iterable[Segment]) -> list[Segment]:
    merged: list[Segment] = []
    for segment in segments:
        if isinstance(segment, Pause) and merged and isinstance(merged[-1], Pause):
            total = merged[-1].seconds + segment.seconds
            if total > MAX_PAUSE_SECONDS:
                raise ValueError(f"Adjacent pauses exceed the {MAX_PAUSE_SECONDS:g}s safety limit.")
            merged[-1] = Pause(total)
        else:
            merged.append(segment)
    return merged


def pause_samples(seconds: float, sample_rate: int) -> int:
    if sample_rate <= 0:
        raise ValueError("Sample rate must be positive.")
    return int(round(seconds * sample_rate))
