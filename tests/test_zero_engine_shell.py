from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_product_shell_can_initialize_without_importing_heavy_engine_or_optional_ml_modules():
    repo_root = Path(__file__).resolve().parents[1]
    script = r'''
import importlib.abc
import sys

blocked = (
    "torch",
    "torchaudio",
    "chatterbox",
    "huggingface_hub",
    "librosa",
    "soundfile",
    "faster_whisper",
    "num2words",
)

class BlockHeavy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise ImportError("heavy/optional module imported during shell initialization: " + fullname)
        return None

sys.meta_path.insert(0, BlockHeavy())
from app import ChatterboxCreatorStudio
studio = ChatterboxCreatorStudio()
assert studio is not None
import product_app
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
