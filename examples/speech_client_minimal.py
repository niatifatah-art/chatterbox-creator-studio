from __future__ import annotations

import argparse
from pathlib import Path

from studio.rpc_client import SpeechRpcClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal model-free client for the local Voice Studio Speech Core.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="Speech Core data directory to share with the target Studio installation.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    with SpeechRpcClient.python_module(args.data_dir) as client:
        info = client.ensure_compatible()
        print(f"Speech Core protocol {info['rpc_protocol_version']} / schema {info['speech_schema_version']}")

        capabilities = client.capabilities()
        print(f"Capabilities: {len(capabilities)}")

        supported = client.engines(include_catalogued=False)
        print("Supported engines:", ", ".join(row["engine_id"] for row in supported) or "none")

        route = client.route_decide(
            capability="speech.synthesize.v1",
            language="en",
            needs_voice_clone=True,
            installed_engines=[row["engine_id"] for row in supported],
        )
        print("Example route:", route["engine_id"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
