from __future__ import annotations

import argparse
from pathlib import Path

from studio.paths import resolve_storage_root, speech_core_data_dir
from studio.rpc import run_stdio_server


def _default_data_dir() -> Path:
    source_root = Path(__file__).resolve().parents[1]
    return speech_core_data_dir(resolve_storage_root(source_root))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local Voice Studio Speech Core JSON-RPC server over stdin/stdout.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=_default_data_dir(),
        help="Local Speech Core data directory. External clients should pass this explicitly when sharing a Studio installation.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_stdio_server(args.data_dir)


if __name__ == "__main__":
    raise SystemExit(main())
