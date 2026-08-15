from __future__ import annotations

import argparse
import json
from pathlib import Path

from .batch import load_batch
from .batch_runner import run_batch
from .diagnostics import collect_diagnostics, format_diagnostics
from .engine import ChatterboxEngine
from .models import MODEL_SPECS
from .reliability import GenerationPolicy, generate_reliably
from .voices import VoiceLibrary


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _engine() -> ChatterboxEngine:
    return ChatterboxEngine(_root() / "outputs")


def _voice_path(value: str) -> Path:
    candidate = Path(value)
    if candidate.exists():
        return candidate
    library = VoiceLibrary(_root() / "data" / "voices")
    resolved = library.path_for(value)
    if resolved is None:
        raise FileNotFoundError(f"Voice '{value}' was not found as a path or saved profile.")
    return resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chatterbox-studio", description="Local Chatterbox Creator Studio CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("models", help="List supported Chatterbox models")
    sub.add_parser("voices", help="List saved voice profiles")
    sub.add_parser("diagnostics", help="Print local environment diagnostics")

    generate = sub.add_parser("generate", help="Generate one voice take")
    generate.add_argument("--voice", required=True, help="Saved voice name or WAV path")
    text_group = generate.add_mutually_exclusive_group(required=True)
    text_group.add_argument("--text")
    text_group.add_argument("--file")
    generate.add_argument("--model", choices=MODEL_SPECS, default="multilingual-v3")
    generate.add_argument("--language", default="en")
    generate.add_argument("--seed", type=int, default=-1)
    generate.add_argument("--raw", action="store_true")
    generate.add_argument("--quality-check", action="store_true")
    generate.add_argument("--verify-stt", action="store_true")
    generate.add_argument("--retries", type=int, default=0)
    generate.add_argument("--best-of", type=int, default=1)

    batch = sub.add_parser("batch", help="Generate TXT/CSV/JSON/SRT/VTT batch")
    batch.add_argument("file")
    batch.add_argument("--voice", required=True)
    batch.add_argument("--model", choices=MODEL_SPECS, default="multilingual-v3")
    batch.add_argument("--language", default="en")
    batch.add_argument("--fit-timing", action="store_true")
    batch.add_argument("--quality-check", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = _root()

    if args.command == "models":
        for model_id, spec in MODEL_SPECS.items():
            print(f"{model_id:18} {spec.name} — {spec.description}")
        return 0
    if args.command == "voices":
        library = VoiceLibrary(root / "data" / "voices")
        for profile in library.profiles():
            duration = f"{profile.duration_seconds:.1f}s" if profile.duration_seconds is not None else "?"
            print(f"{profile.name:24} {duration} {profile.warning or ''}".rstrip())
        return 0
    if args.command == "diagnostics":
        print(format_diagnostics(collect_diagnostics(root)))
        return 0

    engine = _engine()
    voice = _voice_path(args.voice)
    if args.command == "generate":
        script = args.text if args.text is not None else Path(args.file).read_text(encoding="utf-8")
        result = generate_reliably(
            engine,
            script,
            policy=GenerationPolicy(
                quality_check=bool(args.quality_check),
                verify_stt=bool(args.verify_stt),
                auto_retries=max(0, args.retries),
                best_of_n=max(1, args.best_of),
            ),
            voice_path=voice,
            model_id=args.model,
            language_id=args.language,
            seed=args.seed,
            raw_mode=bool(args.raw),
        )
        print(result.selected.result.audio_path)
        print(f"seed={result.selected.result.seed} score={result.selected.score:.3f} candidates={len(result.candidates)}")
        return 0
    if args.command == "batch":
        items = load_batch(args.file)
        summary = run_batch(
            engine,
            items,
            root / "outputs" / "batches",
            generation_kwargs={
                "voice_path": voice,
                "model_id": args.model,
                "language_id": args.language,
                "seed": -1,
            },
            policy=GenerationPolicy(quality_check=bool(args.quality_check)),
            fit_to_timing=bool(args.fit_timing),
        )
        print(json.dumps({
            "output_dir": str(summary.output_dir),
            "manifest": str(summary.manifest_path),
            "generated": summary.generated,
            "failed": summary.failed,
        }, indent=2))
        return 0
    return 1
