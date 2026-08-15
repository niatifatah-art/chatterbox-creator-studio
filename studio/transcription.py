from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str | None
    language_probability: float | None
    duration_seconds: float | None
    segments: tuple[dict, ...]


@lru_cache(maxsize=4)
def _load_model(model_size: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, device=device, compute_type=compute_type)


def clear_transcription_cache() -> None:
    _load_model.cache_clear()


def _resolve_device(preference: str = "auto") -> tuple[str, str]:
    normalized = (preference or "auto").lower()
    if normalized == "cpu":
        return "cpu", "int8"
    if normalized in {"gpu", "cuda"}:
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda", "float16"
        except Exception:
            pass
        raise RuntimeError("GPU transcription was requested, but CUDA is not available.")
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def transcribe_audio(
    audio_path: str | Path,
    *,
    mode: str = "Balanced",
    language_id: str | None = None,
    compute_preference: str = "auto",
) -> TranscriptionResult:
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError("Choose an audio file first.")
    try:
        import faster_whisper  # noqa: F401
    except Exception as exc:
        raise RuntimeError("Speech to Text needs the optional local speech tools. Install them from Settings once, then try again.") from exc

    model_size = {"Fast": "tiny", "Balanced": "base", "Best": "small"}.get(mode, "base")
    device, compute_type = _resolve_device(compute_preference)
    model = _load_model(model_size, device, compute_type)
    segments_iter, info = model.transcribe(
        str(path),
        language=language_id or None,
        vad_filter=True,
        beam_size=5 if mode == "Best" else 3,
    )
    rows: list[dict] = []
    texts: list[str] = []
    for segment in segments_iter:
        text = (segment.text or "").strip()
        if not text:
            continue
        texts.append(text)
        rows.append(
            {
                "start": round(float(segment.start), 3),
                "end": round(float(segment.end), 3),
                "text": text,
            }
        )
    duration = rows[-1]["end"] if rows else None
    return TranscriptionResult(
        text=" ".join(texts),
        language=getattr(info, "language", None),
        language_probability=(float(getattr(info, "language_probability", 0.0)) if getattr(info, "language_probability", None) is not None else None),
        duration_seconds=float(duration) if duration is not None else None,
        segments=tuple(rows),
    )
