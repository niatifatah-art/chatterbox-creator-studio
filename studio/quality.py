from __future__ import annotations

import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path


@dataclass(frozen=True)
class QualityReport:
    passed: bool
    score: float
    duration_seconds: float
    silence_ratio: float
    clipping_ratio: float
    tail_silence_seconds: float
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class VerificationReport:
    available: bool
    passed: bool
    similarity: float | None
    transcript: str
    warning: str | None = None


def _longest_tail_below(signal, threshold: float) -> int:
    count = 0
    for value in reversed(signal):
        if abs(float(value)) <= threshold:
            count += 1
        else:
            break
    return count


def analyze_audio(
    audio_path: str | Path,
    silence_threshold: float = 0.004,
    max_silence_ratio: float = 0.72,
    max_clipping_ratio: float = 0.002,
    max_tail_silence_seconds: float = 4.0,
    min_duration_seconds: float = 0.12,
) -> QualityReport:
    import torch
    import torchaudio as ta

    wav, sample_rate = ta.load(str(audio_path))
    if wav.ndim == 2:
        mono = wav.mean(dim=0)
    else:
        mono = wav.reshape(-1)
    total = int(mono.numel())
    duration = total / float(sample_rate) if sample_rate else 0.0
    if total == 0:
        return QualityReport(False, 0.0, 0.0, 1.0, 0.0, 0.0, ("Empty audio file.",))

    absolute = mono.abs()
    silence_ratio = float((absolute <= silence_threshold).float().mean().item())
    clipping_ratio = float((absolute >= 0.995).float().mean().item())
    tail_samples = _longest_tail_below(mono.tolist(), silence_threshold)
    tail_silence = tail_samples / float(sample_rate)

    warnings: list[str] = []
    if duration < min_duration_seconds:
        warnings.append("Output is suspiciously short.")
    if silence_ratio > max_silence_ratio:
        warnings.append("Output contains an unusually high silence ratio.")
    if clipping_ratio > max_clipping_ratio:
        warnings.append("Output contains clipping or near-clipping samples.")
    if tail_silence > max_tail_silence_seconds:
        warnings.append("Output has a long silent tail.")
    if not torch.isfinite(mono).all().item():
        warnings.append("Output contains non-finite samples.")

    penalties = 0.0
    penalties += min(0.45, max(0.0, silence_ratio - 0.25) * 0.8)
    penalties += min(0.30, clipping_ratio * 25.0)
    penalties += min(0.20, max(0.0, tail_silence - 0.8) / 20.0)
    if duration < min_duration_seconds:
        penalties += 0.35
    score = max(0.0, min(1.0, 1.0 - penalties))
    return QualityReport(
        passed=not warnings,
        score=score,
        duration_seconds=duration,
        silence_ratio=silence_ratio,
        clipping_ratio=clipping_ratio,
        tail_silence_seconds=tail_silence,
        warnings=tuple(warnings),
    )


def _normalize_for_compare(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"\[[^\]]+\]", " ", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def text_similarity(source: str, transcript: str) -> float:
    left = _normalize_for_compare(source)
    right = _normalize_for_compare(transcript)
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return float(SequenceMatcher(None, left, right).ratio())


def verify_with_faster_whisper(
    audio_path: str | Path,
    source_text: str,
    language_id: str | None = None,
    model_size: str = "tiny",
    threshold: float = 0.78,
) -> VerificationReport:
    """Optional local verification. No dependency is installed unless the user opts in."""
    try:
        from faster_whisper import WhisperModel
    except Exception:
        return VerificationReport(
            available=False,
            passed=False,
            similarity=None,
            transcript="",
            warning="Install requirements-optional.txt to enable Faster-Whisper verification.",
        )

    try:
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(
            str(audio_path),
            language=language_id if language_id and language_id not in {"zh", "ja"} else None,
            vad_filter=True,
        )
        transcript = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
        similarity = text_similarity(source_text, transcript)
        return VerificationReport(
            available=True,
            passed=similarity >= threshold,
            similarity=similarity,
            transcript=transcript,
            warning=None if similarity >= threshold else "Transcript differs substantially from the source text.",
        )
    except Exception as exc:
        return VerificationReport(
            available=True,
            passed=False,
            similarity=None,
            transcript="",
            warning=f"STT verification failed: {exc}",
        )
