from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any


def collect_diagnostics(root: str | Path | None = None) -> dict[str, Any]:
    root_path = Path(root).resolve() if root else Path.cwd().resolve()
    payload: dict[str, Any] = {
        "python": sys.version.replace("\n", " "),
        "executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "working_directory": str(root_path),
        "ffmpeg": shutil.which("ffmpeg"),
        "git": shutil.which("git"),
        "hf_home": os.getenv("HF_HOME") or str(Path.home() / ".cache" / "huggingface"),
    }
    try:
        import torch

        payload["torch"] = torch.__version__
        payload["cuda_available"] = bool(torch.cuda.is_available())
        payload["mps_available"] = bool(hasattr(torch.backends, "mps") and torch.backends.mps.is_available())
        if torch.cuda.is_available():
            payload["cuda_version"] = torch.version.cuda
            payload["gpu"] = torch.cuda.get_device_name(0)
            try:
                props = torch.cuda.get_device_properties(0)
                payload["vram_gb"] = round(props.total_memory / 1024**3, 2)
            except Exception:
                pass
    except Exception as exc:
        payload["torch_error"] = str(exc)

    try:
        import gradio

        payload["gradio"] = gradio.__version__
    except Exception:
        pass
    try:
        import chatterbox

        payload["chatterbox_module"] = str(Path(chatterbox.__file__).resolve())
    except Exception as exc:
        payload["chatterbox_error"] = str(exc)

    try:
        usage = shutil.disk_usage(root_path)
        payload["disk_free_gb"] = round(usage.free / 1024**3, 2)
        payload["disk_total_gb"] = round(usage.total / 1024**3, 2)
    except Exception:
        pass
    return payload


def format_diagnostics(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
