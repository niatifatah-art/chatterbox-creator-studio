from __future__ import annotations

import argparse
import os
from pathlib import Path

from studio.rpc import run_stdio_server


def _default_data_dir() -> Path:
    configured = os.getenv("CREATOR_STUDIO_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".creator-studio"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local Voice Studio Speech Core JSON-RPC server over stdin/stdout.")
    parser.add_argument("--data-dir", type=Path, default=_default_data_dir(), help="Local Speech Core data directory.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_stdio_server(args.data_dir)


if __name__ == "__main__":
    raise SystemExit(main())
