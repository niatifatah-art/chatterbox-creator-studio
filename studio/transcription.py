from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


QUALITY_MODEL_SIZES: dict[str, str] = {
    "Fast": "tiny",
    "Balanced": "base",
    "Best": "small",
}


@dataclass(frozen=True)
class TranscriptionSegment:
    start: float
    end: float
    text: str

    def as_dict(self) -> dict[str, float | str]:
        return {"start": self.start, "end": self.end, "text": self.text}


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str | None
    language_probability: float | None
    duration_seconds: float | None
    segments: tuple[TranscriptionSegment, ...]


def model_size_for_mode(mode: str | None) -> str:
    return QUALITY_MODEL_SIZES.get(str(mode or "Balanced"), "base")


def transcription_model_path(model_size: str, *, local_files_only: bool) -> Path:
    """Resolve a Faster-Whisper model without hiding network behavior.

    Passing a short model size directly to WhisperModel normally downloads the model
    automatically. The product checks locally first and exposes a separate explicit
    download action instead, so pressing Transcribe can never surprise the user with a
    model download.
    """
    try:
        from faster_whisper.utils import download_model
    except Exception as exc:
        raise RuntimeError(
            "Speech to Text needs the optional local speech tools. Install them from Transcribe first."
        ) from exc
    return Path(download_model(model_size, local_files_only=local_files_only))


def transcription_model_ready(mode: str | None = "Balanced") -> bool:
    try:
        return transcription_model_path(model_size_for_mode(mode), local_files_only=True).exists()
    except Exception:
        return False


def download_transcription_model(mode: str | None = "Balanced") -> Path:
    size = model_size_for_mode(mode)
    return transcription_model_path(size, local_files_only=False)


@lru_cache(maxsize=4)
def _load_model(model_size: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    # Never let WhisperModel implicitly reach the network. The product has a separate
    # user-approved download action and this loader only opens an already cached model.
    model_path = transcription_model_path(model_size, local_files_only=True)
    return WhisperModel(str(model_path), device=device, compute_type=compute_type, local_files_only=True)


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
    # Compatibility aliases used by the existing product controller. Keeping these
    # here avoids two subtly different Whisper paths while app.py is being separated
    # from its legacy UI tree.
    model_size: str | None = None,
    language: str | None = None,
    device: str | None = None,
) -> TranscriptionResult:
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError("Choose an audio file first.")
    try:
        import faster_whisper  # noqa: F401
    except Exception as exc:
        raise RuntimeError("Speech to Text needs the optional local speech tools. Install them from Transcribe, then try again.") from exc

    selected_size = model_size or model_size_for_mode(mode)
    if not transcription_model_ready(mode if model_size is None else {"tiny": "Fast", "base": "Balanced", "small": "Best"}.get(selected_size, "Balanced")):
        raise RuntimeError(
            f"The {selected_size} speech model is not downloaded yet. Download it from Transcribe, then try again."
        )
    selected_language = language if language is not None else language_id
    selected_compute = device if device is not None else compute_preference
    resolved_device, compute_type = _resolve_device(selected_compute)
    model = _load_model(selected_size, resolved_device, compute_type)
    segments_iter, info = model.transcribe(
        str(path),
        language=selected_language or None,
        vad_filter=True,
        beam_size=5 if mode == "Best" or selected_size == "small" else 3,
    )
    rows: list[TranscriptionSegment] = []
    texts: list[str] = []
    for segment in segments_iter:
        text = (segment.text or "").strip()
        if not text:
            continue
        texts.append(text)
        rows.append(
            TranscriptionSegment(
                start=round(float(segment.start), 3),
                end=round(float(segment.end), 3),
                text=text,
            )
        )
    duration = rows[-1].end if rows else None
    return TranscriptionResult(
        text=" ".join(texts),
        language=getattr(info, "language", None),
        language_probability=(float(getattr(info, "language_probability", 0.0)) if getattr(info, "language_probability", None) is not None else None),
        duration_seconds=float(duration) if duration is not None else None,
        segments=tuple(rows),
    )
