#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -x .venv/bin/python ]]; then
  echo "Run setup first: bash scripts/setup_linux.sh"
  exit 1
fi

export PYTHONUTF8=1
exec .venv/bin/python product_app.py
