from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CachedModel:
    name: str
    path: Path
    size_bytes: int

    @property
    def size_gb(self) -> float:
        return self.size_bytes / 1024**3


def hf_hub_dir() -> Path:
    root = Path(os.getenv("HF_HOME") or Path.home() / ".cache" / "huggingface")
    return root / "hub"


def _dir_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            pass
    return total


def list_chatterbox_cache() -> list[CachedModel]:
    hub = hf_hub_dir()
    if not hub.exists():
        return []
    items: list[CachedModel] = []
    for path in hub.glob("models--ResembleAI--*"):
        if not path.is_dir() or "chatterbox" not in path.name.lower():
            continue
        items.append(CachedModel(name=path.name, path=path, size_bytes=_dir_size(path)))
    return sorted(items, key=lambda item: item.name.lower())


def delete_cached_model(name: str) -> bool:
    hub = hf_hub_dir().resolve()
    candidate = (hub / Path(name).name).resolve()
    if candidate.parent != hub:
        return False
    if not candidate.name.startswith("models--ResembleAI--") or "chatterbox" not in candidate.name.lower():
        return False
    if not candidate.exists():
        return False
    shutil.rmtree(candidate)
    return True
