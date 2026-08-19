from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_product_core_can_initialize_without_heavy_or_optional_ml_modules():
    """The reusable Core must boot with zero ML runtimes/models installed.

    The browser UI has separate E2E/Windows smoke jobs that install Gradio. This
    model-free gate intentionally exercises the headless product/Core boundary under
    ``requirements-dev.txt`` only; requiring the UI framework here would conflate
    "zero engines" with "zero application dependencies".
    """

    repo_root = Path(__file__).resolve().parents[1]
    script = r'''
import importlib.abc
import sys
import tempfile
from pathlib import Path

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
            raise ImportError("heavy/optional module imported during core initialization: " + fullname)
        return None

sys.meta_path.insert(0, BlockHeavy())

from studio.engine_manager import EngineManager
from studio.rpc import RpcContext
from studio.synthesis import SpeechSynthesisService

with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    engines = EngineManager(root / "management")
    statuses = engines.statuses()
    assert statuses
    assert all(not row.ready for row in statuses if row.engine_id in {"kokoro", "qwen3-tts"})

    speech = SpeechSynthesisService(root / "speech")
    assert speech.data_dir.is_dir()

    rpc = RpcContext(root / "rpc")
    assert rpc.voice_profiles is not None
    assert rpc.artifacts is not None
    assert rpc.synthesis is not None

for name in blocked:
    assert name not in sys.modules, name
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
