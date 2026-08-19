from __future__ import annotations

"""Isolated Kokoro inference worker.

This module is executed with the Kokoro runtime's Python interpreter. It deliberately
accepts only local model/voice paths and forces Hub/Transformers offline mode so a speech
request can never trigger a hidden network download.
"""

import argparse
import json
import os
import re
import wave
from pathlib import Path


VOICE_ID_RE = re.compile(r"^[ab][fm]_[a-z0-9_]+$")
SAMPLE_RATE = 24_000


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Voice Studio Kokoro worker")
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--voice-id", required=True)
    parser.add_argument("--text-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--device", default="cpu")
    return parser.parse_args()


def _safe_model_files(model_dir: Path, voice_id: str) -> tuple[Path, Path, Path]:
    if not VOICE_ID_RE.fullmatch(voice_id):
        raise ValueError("Invalid Kokoro ready voice id.")
    config = model_dir / "config.json"
    weights = model_dir / "kokoro-v1_0.pth"
    voice = model_dir / "voices" / f"{voice_id}.pt"
    for path in (config, weights, voice):
        if not path.is_file():
            raise FileNotFoundError(f"Required Kokoro asset is missing: {path.name}")
    return config, weights, voice


def _write_pcm16(path: Path, samples) -> None:
    import numpy as np

    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        raise RuntimeError("Kokoro produced empty audio.")
    audio = np.clip(audio, -1.0, 1.0)
    pcm = (audio * 32767.0).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())


def main() -> int:
    args = _parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    model_dir = Path(args.model_dir).expanduser().resolve()
    text_path = Path(args.text_file).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    metadata_path = Path(args.metadata).expanduser().resolve()
    config_path, weights_path, voice_path = _safe_model_files(model_dir, args.voice_id)

    text = text_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("Text is required.")
    if not 0.5 <= float(args.speed) <= 2.0:
        raise ValueError("Kokoro speed must be between 0.5 and 2.0.")

    # Heavy imports remain inside this isolated process only.
    import numpy as np
    import torch
    from kokoro import KModel, KPipeline

    device = str(args.device or "cpu").lower()
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available in the Kokoro runtime.")
    if device == "mps" and not (
        hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
    ):
        raise RuntimeError("MPS was requested but is not available in the Kokoro runtime.")

    repo_id = "hexgrad/Kokoro-82M"
    model = KModel(
        repo_id=repo_id,
        config=str(config_path),
        model=str(weights_path),
    ).to(device).eval()
    lang_code = "b" if args.voice_id.startswith("b") else "a"
    pipeline = KPipeline(lang_code=lang_code, repo_id=repo_id, model=model, device=device)
    voice_tensor = torch.load(str(voice_path), map_location="cpu", weights_only=True)

    chunks: list[np.ndarray] = []
    graphemes: list[str] = []
    generator = pipeline(text, voice=voice_tensor, speed=float(args.speed), split_pattern=r"\n+")
    for result in generator:
        audio = getattr(result, "audio", None)
        grapheme = getattr(result, "graphemes", None)
        if audio is None and isinstance(result, tuple) and len(result) >= 3:
            grapheme, _phonemes, audio = result[:3]
        if audio is None:
            continue
        if hasattr(audio, "detach"):
            audio = audio.detach().cpu().numpy()
        array = np.asarray(audio, dtype=np.float32).reshape(-1)
        if array.size:
            chunks.append(array)
            if grapheme:
                graphemes.append(str(grapheme))

    if not chunks:
        raise RuntimeError("Kokoro produced no audio chunks.")

    if len(chunks) == 1:
        combined = chunks[0]
    else:
        gap = np.zeros(int(SAMPLE_RATE * 0.05), dtype=np.float32)
        pieces: list[np.ndarray] = []
        for index, chunk in enumerate(chunks):
            if index:
                pieces.append(gap)
            pieces.append(chunk)
        combined = np.concatenate(pieces)

    _write_pcm16(output, combined)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(
            {
                "engine_id": "kokoro",
                "model_id": "kokoro-v1.0",
                "voice_id": args.voice_id,
                "lang_code": lang_code,
                "sample_rate": SAMPLE_RATE,
                "chunk_count": len(chunks),
                "speed": float(args.speed),
                "generated_text_chunks": graphemes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
