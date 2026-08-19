from __future__ import annotations

import sys
from pathlib import Path

from studio.qwen_adapter import QwenExecutionAdapter
from studio.runtime_manager import RuntimeStatus


class FakeRuntimeManager:
    def status(self, runtime_id: str) -> RuntimeStatus:
        assert runtime_id == "qwen3-tts"
        return RuntimeStatus(
            runtime_id=runtime_id,
            configured=True,
            installed=True,
            ready=True,
            install_mode="isolated",
            python_path=sys.executable,
        )


def _model_snapshot(root: Path) -> Path:
    snapshot = root / "model"
    (snapshot / "speech_tokenizer").mkdir(parents=True)
    for relative in (
        "config.json",
        "model.safetensors",
        "speech_tokenizer/config.json",
        "speech_tokenizer/model.safetensors",
    ):
        (snapshot / relative).write_bytes(b"test")
    return snapshot


def _verbose_fake_worker(path: Path) -> None:
    path.write_text(
        r'''from __future__ import annotations
import argparse
import json
import os
import struct
import sys
import wave
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--model-dir")
parser.add_argument("--model-id")
parser.add_argument("--text-file")
parser.add_argument("--output")
parser.add_argument("--metadata")
parser.add_argument("--language")
parser.add_argument("--device")
parser.add_argument("--voice-id")
parser.add_argument("--reference-audio")
parser.add_argument("--reference-text-file")
parser.add_argument("--instruct-file")
args = parser.parse_args()

assert os.environ.get("HF_HUB_OFFLINE") == "1"
assert os.environ.get("TRANSFORMERS_OFFLINE") == "1"
assert os.environ.get("TOKENIZERS_PARALLELISM") == "false"
# Deliberately exceed common OS pipe buffer sizes. A parent that calls poll()/wait()
# without draining stderr can deadlock here.
sys.stderr.write("qwen diagnostic line\n" * 20000)
sys.stderr.flush()

out = Path(args.output)
out.parent.mkdir(parents=True, exist_ok=True)
with wave.open(str(out), "wb") as handle:
    handle.setnchannels(1)
    handle.setsampwidth(2)
    handle.setframerate(8000)
    for _ in range(800):
        handle.writeframesraw(struct.pack("<h", 100))
Path(args.metadata).write_text(json.dumps({"chunk_count": 1}), encoding="utf-8")
''',
        encoding="utf-8",
    )


def test_qwen_adapter_drains_verbose_worker_pipes_and_forces_offline_environment(tmp_path: Path):
    worker = tmp_path / "worker.py"
    _verbose_fake_worker(worker)
    adapter = QwenExecutionAdapter(
        FakeRuntimeManager(),  # type: ignore[arg-type]
        worker_path=worker,
        timeout_seconds=10,
    )

    result = adapter.synthesize(
        text="Hello from a model-free subprocess test.",
        model_snapshot=_model_snapshot(tmp_path),
        model_id="qwen3-0.6b-custom",
        language="en",
        output_dir=tmp_path / "output",
        voice_id="Ryan",
        device="cpu",
    )

    assert result.model_id == "qwen3-0.6b-custom"
    assert result.audio_path.is_file()
    assert result.audio_path.stat().st_size > 44
    assert result.metadata_path.is_file()
