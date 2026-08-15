#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v python3.11 >/dev/null 2>&1; then
  echo "Python 3.11 is required. Install it, then rerun: bash scripts/setup_linux.sh"
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  python3.11 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -r requirements.txt

echo
echo "Setup complete. Start with: bash scripts/start_linux.sh"
echo "Models are managed from inside the app and download only when you choose to install or use them."
