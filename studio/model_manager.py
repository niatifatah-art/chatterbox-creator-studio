from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


ProgressCallback = Callable[[int, int | None, str], None]


@dataclass(frozen=True)
class ModelDownloadSpec:
    model_id: str
    repo_id: str
    revision: str = "main"
    allow_patterns: tuple[str, ...] = ()


MODEL_DOWNLOAD_SPECS: dict[str, ModelDownloadSpec] = {
    "multilingual-v3": ModelDownloadSpec(
        model_id="multilingual-v3",
        repo_id="ResembleAI/chatterbox",
        revision="main",
        allow_patterns=(
            "ve.pt",
            "t3_mtl23ls_v3.safetensors",
            "s3gen.pt",
            "grapheme_mtl_merged_expanded_v1.json",
            "conds.pt",
            "Cangjie5_TC.json",
        ),
    ),
    "turbo": ModelDownloadSpec(
        model_id="turbo",
        repo_id="ResembleAI/chatterbox-turbo",
        revision="main",
        allow_patterns=("*.safetensors", "*.json", "*.txt", "*.pt", "*.model"),
    ),
    "nano": ModelDownloadSpec(
        model_id="nano",
        repo_id="ResembleAI/chatterbox-nano",
        revision="main",
        allow_patterns=("*.safetensors", "*.json", "*.txt", "*.pt", "*.model"),
    ),
}


@dataclass(frozen=True)
class LocalModelStatus:
    model_id: str
    installed: bool
    snapshot_path: str | None
    revision: str | None
    size_gb: float
    update_available: bool | None = None
    remote_revision: str | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hf_hub_dir() -> Path:
    configured = os.getenv("HF_HUB_CACHE")
    if configured:
        return Path(configured).expanduser()
    hf_home = Path(os.getenv("HF_HOME") or (Path.home() / ".cache" / "huggingface")).expanduser()
    return hf_home / "hub"


def _repo_cache_name(repo_id: str) -> str:
    owner, name = repo_id.split("/", 1)
    return f"models--{owner}--{name}"


def _size_gb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = 0
    try:
        for child in path.rglob("*"):
            try:
                if child.is_file():
                    total += child.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0.0
    return round(total / 1024**3, 3)


def set_hf_offline(enabled: bool) -> None:
    """Apply the Hub offline flag now and for imports that happen later."""
    if enabled:
        os.environ["HF_HUB_OFFLINE"] = "1"
    else:
        os.environ.pop("HF_HUB_OFFLINE", None)

    # huggingface_hub reads the environment into constants at import time. If it
    # has already been imported, keep the runtime constant in sync too.
    module = sys.modules.get("huggingface_hub.constants")
    if module is not None and hasattr(module, "HF_HUB_OFFLINE"):
        module.HF_HUB_OFFLINE = bool(enabled)


class LocalModelManager:
    """Friendly local model state on top of the Hugging Face cache.

    The manager records the exact snapshot selected by this app. Generation can
    then load that local snapshot directly, so a newer upstream revision never
    silently replaces a creator's working model.
    """

    def __init__(self, state_path: str | Path):
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return {"schema_version": 1, "models": {}}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": 1, "models": {}}
        if not isinstance(payload, dict):
            return {"schema_version": 1, "models": {}}
        payload.setdefault("schema_version", 1)
        payload.setdefault("models", {})
        return payload

    def _save_state(self, payload: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_path)

    @staticmethod
    def _cache_dir(spec: ModelDownloadSpec) -> Path:
        return hf_hub_dir() / _repo_cache_name(spec.repo_id)

    @staticmethod
    def _snapshot_from_cache(spec: ModelDownloadSpec) -> tuple[Path | None, str | None]:
        cache = LocalModelManager._cache_dir(spec)
        ref = cache / "refs" / spec.revision
        revision: str | None = None
        try:
            if ref.exists():
                revision = ref.read_text(encoding="utf-8").strip()
        except OSError:
            revision = None
        if revision:
            snapshot = cache / "snapshots" / revision
            if snapshot.exists():
                return snapshot, revision
        snapshots = cache / "snapshots"
        if snapshots.exists():
            try:
                candidates = sorted(
                    (path for path in snapshots.iterdir() if path.is_dir()),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
            except OSError:
                candidates = []
            if candidates:
                return candidates[0], candidates[0].name
        return None, None

    def _selected_snapshot(self, model_id: str) -> tuple[Path | None, str | None]:
        state = self._load_state()
        entry = (state.get("models") or {}).get(model_id) or {}
        selected = entry.get("snapshot_path")
        revision = entry.get("revision")
        if selected:
            path = Path(str(selected))
            if path.exists():
                return path, str(revision or path.name)
        spec = MODEL_DOWNLOAD_SPECS[model_id]
        snapshot, revision = self._snapshot_from_cache(spec)
        if snapshot is not None and revision:
            # Import an existing Hugging Face cache exactly once. From this point
            # on the app follows this selected snapshot, not a moving refs/main.
            self._select_snapshot(model_id, snapshot, revision)
        return snapshot, revision

    def status(self, model_id: str) -> LocalModelStatus:
        if model_id not in MODEL_DOWNLOAD_SPECS:
            raise ValueError(f"Unknown model '{model_id}'.")
        snapshot, revision = self._selected_snapshot(model_id)
        installed = bool(snapshot and snapshot.exists())
        return LocalModelStatus(
            model_id=model_id,
            installed=installed,
            snapshot_path=str(snapshot) if snapshot else None,
            revision=revision,
            size_gb=_size_gb(snapshot) if snapshot else 0.0,
        )

    def statuses(self) -> tuple[LocalModelStatus, ...]:
        return tuple(self.status(model_id) for model_id in MODEL_DOWNLOAD_SPECS)

    def _select_snapshot(self, model_id: str, path: Path, revision: str) -> None:
        state = self._load_state()
        models = state.setdefault("models", {})
        models[model_id] = {
            "snapshot_path": str(path),
            "revision": revision,
            "selected_at": _utc_now(),
        }
        self._save_state(state)

    @staticmethod
    def _tqdm_class(progress: ProgressCallback | None):
        if progress is None:
            return None
        from tqdm.auto import tqdm

        class ProductTqdm(tqdm):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                try:
                    progress(int(self.n), int(self.total) if self.total is not None else None, str(self.desc or "Downloading model"))
                except Exception:
                    pass

            def update(self, n=1):
                result = super().update(n)
                try:
                    progress(int(self.n), int(self.total) if self.total is not None else None, str(self.desc or "Downloading model"))
                except Exception:
                    pass
                return result

        return ProductTqdm

    def download(
        self,
        model_id: str,
        *,
        refresh: bool = False,
        force_files: bool = False,
        offline: bool = False,
        progress: ProgressCallback | None = None,
    ) -> LocalModelStatus:
        if model_id not in MODEL_DOWNLOAD_SPECS:
            raise ValueError(f"Unknown model '{model_id}'.")
        existing = self.status(model_id)
        if existing.installed and not refresh and not force_files:
            return existing
        if offline:
            raise RuntimeError("This model is not installed and Offline mode is on.")

        spec = MODEL_DOWNLOAD_SPECS[model_id]
        set_hf_offline(False)
        from huggingface_hub import snapshot_download

        if progress:
            progress(0, None, "Connecting…")
        kwargs = {
            "repo_id": spec.repo_id,
            "repo_type": "model",
            "revision": spec.revision,
            "allow_patterns": list(spec.allow_patterns),
            "token": os.getenv("HF_TOKEN") or None,
            "force_download": bool(force_files),
        }
        tqdm_class = self._tqdm_class(progress)
        if tqdm_class is not None:
            kwargs["tqdm_class"] = tqdm_class

        try:
            local = Path(snapshot_download(**kwargs))
        except Exception as exc:
            # Match the upstream Turbo/Nano resilience: if an Xet backend error
            # occurs, retry over the standard HTTP/LFS path.
            if "xet" in str(exc).lower() or "hex hash" in str(exc).lower():
                import huggingface_hub.constants as hf_constants

                hf_constants.HF_HUB_DISABLE_XET = True
                local = Path(snapshot_download(**kwargs))
            else:
                raise

        revision = local.name
        self._select_snapshot(model_id, local, revision)
        if progress:
            progress(1, 1, "Ready")
        return self.status(model_id)

    def ensure(
        self,
        model_id: str,
        *,
        auto_download: bool = True,
        offline: bool = False,
        progress: ProgressCallback | None = None,
    ) -> LocalModelStatus:
        status = self.status(model_id)
        if status.installed:
            return status
        if not auto_download:
            raise RuntimeError("This model is not installed yet. Open Models to install it.")
        return self.download(model_id, offline=offline, progress=progress)

    def check_update(self, model_id: str, *, offline: bool = False) -> LocalModelStatus:
        current = self.status(model_id)
        if not current.installed or offline:
            return current
        spec = MODEL_DOWNLOAD_SPECS[model_id]
        try:
            from huggingface_hub import HfApi

            info = HfApi(token=os.getenv("HF_TOKEN") or None).model_info(spec.repo_id, revision=spec.revision)
            remote = str(getattr(info, "sha", "") or "") or None
        except Exception:
            return current
        available = bool(remote and current.revision and remote != current.revision)
        return LocalModelStatus(
            model_id=current.model_id,
            installed=current.installed,
            snapshot_path=current.snapshot_path,
            revision=current.revision,
            size_gb=current.size_gb,
            update_available=available,
            remote_revision=remote,
        )

    def update(self, model_id: str, *, offline: bool = False, progress: ProgressCallback | None = None) -> LocalModelStatus:
        if offline:
            raise RuntimeError("Turn off Offline mode before checking or downloading model updates.")
        # Refresh the tracked remote ref while reusing unchanged cached blobs.
        # This downloads only what the newly selected snapshot needs; it does not
        # blindly re-fetch every model file.
        status = self.download(model_id, refresh=True, force_files=False, offline=False, progress=progress)
        return status

    def remove(self, model_id: str) -> bool:
        if model_id not in MODEL_DOWNLOAD_SPECS:
            return False
        spec = MODEL_DOWNLOAD_SPECS[model_id]
        cache = self._cache_dir(spec)
        root = hf_hub_dir().resolve()
        try:
            resolved = cache.resolve()
        except OSError:
            resolved = cache.absolute()
        if resolved.parent != root or not resolved.name.startswith("models--ResembleAI--"):
            raise RuntimeError("Refusing to remove an unexpected cache path.")
        removed = False
        if cache.exists():
            shutil.rmtree(cache)
            removed = True
        state = self._load_state()
        models = state.setdefault("models", {})
        if model_id in models:
            del models[model_id]
            self._save_state(state)
        return removed
