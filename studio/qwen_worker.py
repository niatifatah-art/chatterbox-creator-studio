from __future__ import annotations

"""Isolated Qwen3-TTS inference worker.

Only local model/reference paths are accepted. Hub/Transformers offline mode is forced
before importing the model stack so speech generation never becomes an implicit download.
"""

import argparse
import json
import os
from pathlib import Path

LANGUAGE_NAMES = {
    "zh": "Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "de": "German",
    "fr": "French",
    "ru": "Russian",
    "pt": "Portuguese",
    "es": "Spanish",
    "it": "Italian",
}

MODEL_MODES = {
    "qwen3-0.6b-base": "clone",
    "qwen3-0.6b-custom": "ready",
    "qwen3-1.7b-voice-design": "design",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Voice Studio Qwen3-TTS worker")
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--model-id", required=True, choices=tuple(MODEL_MODES))
    parser.add_argument("--text-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--voice-id")
    parser.add_argument("--reference-audio")
    parser.add_argument("--reference-text-file")
    parser.add_argument("--instruct-file")
    return parser.parse_args()


def _language_name(code: str) -> str:
    normalized = (code or "").strip().lower().split("-", 1)[0]
    try:
        return LANGUAGE_NAMES[normalized]
    except KeyError as exc:
        raise ValueError(f"Unsupported Qwen3-TTS language '{code}'.") from exc


def _read_optional(path: str | None) -> str:
    if not path:
        return ""
    return Path(path).expanduser().resolve().read_text(encoding="utf-8").strip()


def _validate_model_dir(model_dir: Path) -> None:
    required = (
        model_dir / "config.json",
        model_dir / "model.safetensors",
        model_dir / "speech_tokenizer" / "config.json",
        model_dir / "speech_tokenizer" / "model.safetensors",
    )
    missing = [path.relative_to(model_dir).as_posix() for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Required Qwen3-TTS model assets are missing: " + ", ".join(missing))


def main() -> int:
    args = _parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"

    model_dir = Path(args.model_dir).expanduser().resolve()
    text_path = Path(args.text_file).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    metadata_path = Path(args.metadata).expanduser().resolve()
    if not model_dir.is_dir():
        raise FileNotFoundError("Qwen3-TTS model snapshot is missing.")
    _validate_model_dir(model_dir)

    text = text_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("Text is required.")
    language_name = _language_name(args.language)
    mode = MODEL_MODES[args.model_id]

    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    requested_device = str(args.device or "cpu").lower()
    if requested_device == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is not available in the Qwen runtime.")
        device_map = "cuda:0"
        dtype = torch.bfloat16
    elif requested_device == "mps":
        if not (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()):
            raise RuntimeError("MPS was requested but is not available in the Qwen runtime.")
        device_map = "mps"
        dtype = torch.float32
    else:
        device_map = "cpu"
        dtype = torch.float32

    model = Qwen3TTSModel.from_pretrained(
        str(model_dir),
        device_map=device_map,
        dtype=dtype,
        local_files_only=True,
    )

    metadata: dict[str, object] = {
        "engine_family": "qwen3-tts",
        "model_id": args.model_id,
        "mode": mode,
        "language": language_name,
        "device": requested_device,
    }

    if mode == "ready":
        speaker = (args.voice_id or "").strip()
        if not speaker:
            raise ValueError("Qwen CustomVoice requires a ready voice id.")
        instruct = _read_optional(args.instruct_file)
        kwargs = {
            "text": text,
            "language": language_name,
            "speaker": speaker,
        }
        if instruct:
            kwargs["instruct"] = instruct
        wavs, sample_rate = model.generate_custom_voice(**kwargs)
        metadata["voice_id"] = speaker
        metadata["instruction_applied"] = bool(instruct)
    elif mode == "design":
        instruct = _read_optional(args.instruct_file)
        if not instruct:
            raise ValueError("Qwen VoiceDesign requires a voice description.")
        wavs, sample_rate = model.generate_voice_design(
            text=text,
            language=language_name,
            instruct=instruct,
        )
        metadata["instruction_applied"] = True
    else:
        if not args.reference_audio:
            raise ValueError("Qwen voice cloning requires a local reference audio file.")
        reference = Path(args.reference_audio).expanduser().resolve()
        if not reference.is_file():
            raise FileNotFoundError("Qwen clone reference audio is missing.")
        ref_text = _read_optional(args.reference_text_file)
        clone_kwargs = {
            "text": text,
            "language": language_name,
            "ref_audio": str(reference),
        }
        if ref_text:
            clone_kwargs["ref_text"] = ref_text
            clone_kwargs["x_vector_only_mode"] = False
            metadata["clone_prompt_mode"] = "audio_and_transcript"
        else:
            clone_kwargs["x_vector_only_mode"] = True
            metadata["clone_prompt_mode"] = "speaker_embedding_only"
        wavs, sample_rate = model.generate_voice_clone(**clone_kwargs)

    if not wavs:
        raise RuntimeError("Qwen3-TTS produced no audio.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), wavs[0], int(sample_rate))

    metadata["sample_rate"] = int(sample_rate)
    metadata["chunk_count"] = 1
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
