from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioProcessOptions:
    trim_silence: bool = False
    peak_normalize: bool = False
    target_peak: float = 0.95
    fade_ms: int = 0
    target_duration_seconds: float | None = None
    max_duration_stretch: float = 1.18


def _trim(wav, threshold: float = 0.003):
    import torch

    mono = wav.abs().max(dim=0).values if wav.ndim == 2 else wav.abs()
    indices = torch.nonzero(mono > threshold).flatten()
    if indices.numel() == 0:
        return wav
    start = max(0, int(indices[0]) - 64)
    end = min(wav.shape[-1], int(indices[-1]) + 65)
    return wav[..., start:end]


def _fade(wav, sample_rate: int, fade_ms: int):
    import torch

    samples = min(int(sample_rate * fade_ms / 1000.0), wav.shape[-1] // 2)
    if samples <= 0:
        return wav
    result = wav.clone()
    fade_in = torch.linspace(0.0, 1.0, samples, dtype=result.dtype, device=result.device)
    fade_out = torch.linspace(1.0, 0.0, samples, dtype=result.dtype, device=result.device)
    result[..., :samples] *= fade_in
    result[..., -samples:] *= fade_out
    return result


def _stretch(wav, rate: float):
    if abs(float(rate) - 1.0) < 1e-4:
        return wav
    import librosa
    import numpy as np
    import torch

    channels = []
    for channel in wav.detach().cpu().numpy():
        stretched = librosa.effects.time_stretch(channel.astype(np.float32, copy=False), rate=float(rate))
        channels.append(torch.from_numpy(stretched))
    return torch.stack(channels)


def process_audio(
    source_path: str | Path,
    destination_path: str | Path | None = None,
    options: AudioProcessOptions | None = None,
) -> Path:
    import torchaudio as ta

    options = options or AudioProcessOptions()
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError("Audio file not found.")
    destination = Path(destination_path) if destination_path else source.with_name(f"{source.stem}_processed.wav")

    wav, sample_rate = ta.load(str(source))
    if options.trim_silence:
        wav = _trim(wav)
    if options.target_duration_seconds is not None:
        target = float(options.target_duration_seconds)
        if target <= 0:
            raise ValueError("Target duration must be positive.")
        current = wav.shape[-1] / float(sample_rate)
        if current > 0:
            rate = current / target
            limit = float(options.max_duration_stretch)
            if not (1.0 / limit <= rate <= limit):
                raise ValueError(
                    f"Required duration stretch ({rate:.2f}x) exceeds the safe limit ({limit:.2f}x)."
                )
            wav = _stretch(wav, rate)
    if options.peak_normalize:
        peak = float(wav.abs().max().item()) if wav.numel() else 0.0
        if peak > 1e-8:
            wav = wav * (float(options.target_peak) / peak)
    if int(options.fade_ms) > 0:
        wav = _fade(wav, sample_rate, int(options.fade_ms))

    destination.parent.mkdir(parents=True, exist_ok=True)
    ta.save(str(destination), wav.cpu(), int(sample_rate))
    return destination


def export_audio(source_path: str | Path, destination_path: str | Path) -> Path:
    """Export WAV/FLAC directly; MP3 works when the local torchaudio backend supports it."""
    import torchaudio as ta

    source = Path(source_path)
    destination = Path(destination_path)
    wav, sample_rate = ta.load(str(source))
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        ta.save(str(destination), wav, int(sample_rate))
    except Exception as exc:
        raise RuntimeError(
            f"Could not export {destination.suffix or 'audio'} on this machine. "
            "WAV is always recommended; MP3 may require an FFmpeg-enabled audio backend."
        ) from exc
    return destination
