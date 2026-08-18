from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from .engine import GenerationResult
from .preprocess import PreprocessOptions, ProcessedText, process_text
from .quality import QualityReport, VerificationReport, analyze_audio, verify_with_faster_whisper


class GenerationEngine(Protocol):
    """Structural contract used by reliability/Best-of orchestration.

    Reliability owns candidate policy; Speech Core owns synthesis. Keeping this module
    structurally typed prevents another engine family from becoming a dependency here.
    """

    def generate(self, script: str, **generation_kwargs: Any) -> GenerationResult: ...


@dataclass(frozen=True)
class GenerationPolicy:
    preprocess: PreprocessOptions = PreprocessOptions()
    quality_check: bool = False
    verify_stt: bool = False
    whisper_model: str = "tiny"
    verification_threshold: float = 0.78
    auto_retries: int = 0
    best_of_n: int = 1


@dataclass(frozen=True)
class Candidate:
    result: GenerationResult
    quality: QualityReport | None
    verification: VerificationReport | None
    score: float


@dataclass(frozen=True)
class ReliableGenerationResult:
    selected: Candidate
    candidates: tuple[Candidate, ...]
    processed_text: ProcessedText


def _candidate_score(quality: QualityReport | None, verification: VerificationReport | None) -> float:
    if quality is None and (verification is None or verification.similarity is None):
        return 1.0
    if quality is None:
        return float(verification.similarity or 0.0)
    if verification is None or verification.similarity is None:
        return float(quality.score)
    return float(quality.score) * 0.65 + float(verification.similarity) * 0.35


def _candidate_passes(candidate: Candidate, policy: GenerationPolicy) -> bool:
    quality_ok = True if not policy.quality_check else bool(candidate.quality and candidate.quality.passed)
    verify_ok = True if not policy.verify_stt else bool(candidate.verification and candidate.verification.passed)
    return quality_ok and verify_ok


def _annotate_metadata(
    candidate: Candidate,
    selected: bool,
    processed: ProcessedText,
    policy: GenerationPolicy,
    implicit_best_of_quality: bool,
) -> None:
    path = candidate.result.metadata_path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    payload["studio_reliability"] = {
        "selected": bool(selected),
        "score": candidate.score,
        "preprocessed": processed.changed,
        "preprocess_warnings": list(processed.warnings),
        "quality_check_requested": bool(policy.quality_check),
        "quality_used_for_best_of": bool(implicit_best_of_quality),
        "verification_requested": bool(policy.verify_stt),
        "quality": candidate.quality.__dict__ if candidate.quality else None,
        "verification": candidate.verification.__dict__ if candidate.verification else None,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def generate_reliably(
    engine: GenerationEngine,
    script: str,
    policy: GenerationPolicy | None = None,
    **generation_kwargs: Any,
) -> ReliableGenerationResult:
    policy = policy or GenerationPolicy()
    language_id = str(generation_kwargs.get("language_id") or "en")
    processed = process_text(script, language_id=language_id, options=policy.preprocess)
    base_seed = generation_kwargs.get("seed", -1)
    requested_best = max(1, min(5, int(policy.best_of_n)))
    retry_budget = max(0, min(5, int(policy.auto_retries)))
    target_attempts = requested_best if requested_best > 1 else 1 + retry_budget

    # Best-of needs an objective. If the user explicitly asks for multiple candidates
    # but leaves STT/quality toggles off, use the cheap non-transforming audio QC score
    # for ranking. This does not alter any candidate and is recorded in metadata.
    implicit_best_of_quality = requested_best > 1 and not policy.quality_check and not policy.verify_stt
    run_quality = bool(policy.quality_check or implicit_best_of_quality)

    candidates: list[Candidate] = []
    for attempt in range(target_attempts):
        kwargs = dict(generation_kwargs)
        if base_seed is not None and int(base_seed) >= 0:
            kwargs["seed"] = int(base_seed) + attempt
        else:
            kwargs["seed"] = -1
        result = engine.generate(script=processed.processed, **kwargs)
        quality = analyze_audio(result.audio_path) if run_quality else None
        verification = (
            verify_with_faster_whisper(
                result.audio_path,
                processed.processed,
                language_id=language_id,
                model_size=policy.whisper_model,
                threshold=float(policy.verification_threshold),
            )
            if policy.verify_stt
            else None
        )
        candidate = Candidate(
            result=result,
            quality=quality,
            verification=verification,
            score=_candidate_score(quality, verification),
        )
        candidates.append(candidate)
        if requested_best == 1 and _candidate_passes(candidate, policy):
            break

    selected = max(candidates, key=lambda candidate: candidate.score)
    for candidate in candidates:
        _annotate_metadata(candidate, candidate is selected, processed, policy, implicit_best_of_quality)
    return ReliableGenerationResult(
        selected=selected,
        candidates=tuple(candidates),
        processed_text=processed,
    )
