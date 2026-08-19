from __future__ import annotations

import fnmatch
import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from studio.engine_assets import (
    MODEL_ASSET_MANIFESTS,
    ModelAssetManifest,
    ModelProvider,
    model_asset_manifest,
)


ProgressCallback = Callable[[int, int | None, str], None]
MODEL_STATE_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class ModelDownloadSpec:
    """Backward-compatible view of a model asset manifest.

    Older controllers/scripts still import this shape. New management code should use
    ``ModelAssetManifest`` so engine, runtime and model identity stay separate.
    """

    model_id: str
    repo_id: str
    revision: str = "main"
    allow_patterns: tuple[str, ...] = ()


MODEL_DOWNLOAD_SPECS: dict[str, ModelDownloadSpec] = {
    model_id: ModelDownloadSpec(
        model_id=model_id,
        repo_id=manifest.repo_id,
        revision=manifest.revision_ref,
        allow_patterns=manifest.allow_patterns,
    )
    for model_id, manifest in MODEL_ASSET_MANIFESTS.items()
}


@dataclass(frozen=True)
class ModelVerificationReport:
    model_id: str
    valid: bool
    snapshot_path: str | None
    revision: str | None
    source_trusted: bool | None
    missing_files: tuple[str, ...] = ()
    warning: str | None = None


@dataclass(frozen=True)
class LocalModelStatus:
    model_id: str
    installed: bool
    snapshot_path: str | None
    revision: str | None
    size_gb: float
    update_available: bool | None = None
    remote_revision: str | None = None
    source_trusted: bool | None = None
    repairable: bool = False
    verification_warning: str | None = None
    candidate_snapshot_path: str | None = None
    candidate_revision: str | None = None
    previous_snapshot_path: str | None = None
    previous_revision: str | None = None


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


def _size_gb(path: Path | None) -> float:
    if path is None or not path.exists():
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

    module = sys.modules.get("huggingface_hub.constants")
    if module is not None and hasattr(module, "HF_HUB_OFFLINE"):
        module.HF_HUB_OFFLINE = bool(enabled)


class LocalModelManager:
    """Safe local model lifecycle on top of provider-managed caches.

    Selected revisions are durable and immutable from the product's point of view.
    Updates are staged as candidates, verified, then explicitly promoted while the
    former selection is retained for rollback. Read-only status methods never perform
    network requests.
    """

    def __init__(self, state_path: str | Path):
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _empty_state() -> dict:
        return {"schema_version": MODEL_STATE_SCHEMA_VERSION, "models": {}}

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return self._empty_state()
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty_state()
        if not isinstance(payload, dict):
            return self._empty_state()
        payload.setdefault("models", {})
        if not isinstance(payload["models"], dict):
            payload["models"] = {}

        version = int(payload.get("schema_version") or 1)
        if version > MODEL_STATE_SCHEMA_VERSION:
            raise RuntimeError(
                f"Model state schema {version} is newer than this application supports."
            )
        if version < 2:
            # v1 already stored the selected snapshot at the top level. v2 preserves
            # those keys exactly and only adds candidate/previous lifecycle slots.
            payload["schema_version"] = 2
            for entry in payload["models"].values():
                if isinstance(entry, dict):
                    entry.setdefault("candidate", None)
                    entry.setdefault("previous", None)
            self._save_state(payload)
        return payload

    def _save_state(self, payload: dict) -> None:
        payload = dict(payload)
        payload["schema_version"] = MODEL_STATE_SCHEMA_VERSION
        payload.setdefault("models", {})
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_path)

    @staticmethod
    def _manifest(model_id: str) -> ModelAssetManifest:
        return model_asset_manifest(model_id)

    @staticmethod
    def _cache_dir(manifest: ModelAssetManifest) -> Path:
        if manifest.provider != ModelProvider.HUGGINGFACE:
            raise ValueError(f"Unsupported model provider '{manifest.provider.value}'.")
        return hf_hub_dir() / _repo_cache_name(manifest.repo_id)

    @staticmethod
    def _snapshot_from_cache(manifest: ModelAssetManifest) -> tuple[Path | None, str | None]:
        """Resolve an already-cached snapshot without any network call."""

        cache = LocalModelManager._cache_dir(manifest)
        ref = cache / "refs" / manifest.revision_ref
        revision: str | None = None
        try:
            if ref.exists():
                revision = ref.read_text(encoding="utf-8").strip()
        except OSError:
            revision = None
        if revision:
            snapshot = cache / "snapshots" / revision
            if snapshot.is_dir():
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

    @staticmethod
    def _is_managed_snapshot(manifest: ModelAssetManifest, path: Path, revision: str | None) -> bool:
        try:
            resolved = path.expanduser().resolve()
            root = (LocalModelManager._cache_dir(manifest) / "snapshots").resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            return False
        if resolved.parent != root:
            return False
        if revision and resolved.name != revision:
            return False
        return True

    @staticmethod
    def _matches_any(relative_path: str, patterns: tuple[str, ...]) -> bool:
        normalized = relative_path.replace("\\", "/")
        return any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns)

    @classmethod
    def _verify_path(
        cls,
        model_id: str,
        path: Path | None,
        revision: str | None,
    ) -> ModelVerificationReport:
        manifest = cls._manifest(model_id)
        if path is None or not path.is_dir():
            return ModelVerificationReport(
                model_id=model_id,
                valid=False,
                snapshot_path=str(path) if path else None,
                revision=revision,
                source_trusted=None if path is None else cls._is_managed_snapshot(manifest, path, revision),
                warning="Selected model snapshot is missing.",
            )

        missing: list[str] = []
        for expected in manifest.expected_files:
            if not (path / expected).is_file():
                missing.append(expected)

        # Some upstream repositories contain multiple possible filenames. When there is
        # no exact expected-file list, require at least one file matching the allowlist
        # (if an allowlist exists) so an empty/corrupt directory is not treated as ready.
        if not manifest.expected_files and manifest.allow_patterns:
            matched = False
            try:
                for candidate in path.rglob("*"):
                    if not candidate.is_file():
                        continue
                    rel = candidate.relative_to(path).as_posix()
                    if cls._matches_any(rel, manifest.allow_patterns):
                        matched = True
                        break
            except OSError:
                matched = False
            if not matched:
                missing.append("<matching model asset>")

        trusted = cls._is_managed_snapshot(manifest, path, revision)
        valid = not missing
        warning = None
        if missing:
            preview = ", ".join(missing[:4])
            suffix = "…" if len(missing) > 4 else ""
            warning = f"Missing expected model files: {preview}{suffix}"
        return ModelVerificationReport(
            model_id=model_id,
            valid=valid,
            snapshot_path=str(path),
            revision=revision,
            source_trusted=trusted,
            missing_files=tuple(missing),
            warning=warning,
        )

    def _entry(self, model_id: str) -> dict:
        state = self._load_state()
        entry = (state.get("models") or {}).get(model_id)
        return dict(entry) if isinstance(entry, dict) else {}

    def _selected_snapshot(self, model_id: str) -> tuple[Path | None, str | None]:
        manifest = self._manifest(model_id)
        state = self._load_state()
        entry = (state.get("models") or {}).get(model_id) or {}
        selected = entry.get("snapshot_path")
        revision = entry.get("revision")
        if selected:
            # Once the user/app selected a revision, preserve that exact identity even
            # when files later disappear. Status must report repairable/missing rather
            # than silently switching the voice to a different cached revision.
            return Path(str(selected)).expanduser(), str(revision or Path(str(selected)).name)

        snapshot, revision = self._snapshot_from_cache(manifest)
        if snapshot is not None and revision:
            self._select_snapshot(model_id, snapshot, revision)
        return snapshot, revision

    def _status_from_selected(
        self,
        model_id: str,
        snapshot: Path | None,
        revision: str | None,
        *,
        update_available: bool | None = None,
        remote_revision: str | None = None,
    ) -> LocalModelStatus:
        report = self._verify_path(model_id, snapshot, revision)
        state = self._load_state()
        entry = (state.get("models") or {}).get(model_id) or {}
        candidate = entry.get("candidate") if isinstance(entry.get("candidate"), dict) else {}
        previous = entry.get("previous") if isinstance(entry.get("previous"), dict) else {}
        installed = bool(snapshot and snapshot.is_dir())
        return LocalModelStatus(
            model_id=model_id,
            installed=installed,
            snapshot_path=str(snapshot) if snapshot else None,
            revision=revision,
            size_gb=_size_gb(snapshot),
            update_available=update_available,
            remote_revision=remote_revision,
            source_trusted=report.source_trusted if installed else None,
            repairable=bool(installed and not report.valid),
            verification_warning=report.warning,
            candidate_snapshot_path=str(candidate.get("snapshot_path")) if candidate.get("snapshot_path") else None,
            candidate_revision=str(candidate.get("revision")) if candidate.get("revision") else None,
            previous_snapshot_path=str(previous.get("snapshot_path")) if previous.get("snapshot_path") else None,
            previous_revision=str(previous.get("revision")) if previous.get("revision") else None,
        )

    def status(self, model_id: str) -> LocalModelStatus:
        self._manifest(model_id)
        snapshot, revision = self._selected_snapshot(model_id)
        return self._status_from_selected(model_id, snapshot, revision)

    def statuses(self) -> tuple[LocalModelStatus, ...]:
        return tuple(self.status(model_id) for model_id in MODEL_ASSET_MANIFESTS)

    def verify(self, model_id: str) -> ModelVerificationReport:
        snapshot, revision = self._selected_snapshot(model_id)
        return self._verify_path(model_id, snapshot, revision)

    def _select_snapshot(self, model_id: str, path: Path, revision: str) -> None:
        state = self._load_state()
        models = state.setdefault("models", {})
        old = models.get(model_id) if isinstance(models.get(model_id), dict) else {}
        models[model_id] = {
            **old,
            "snapshot_path": str(path),
            "revision": revision,
            "selected_at": _utc_now(),
            "candidate": old.get("candidate"),
            "previous": old.get("previous"),
        }
        self._save_state(state)

    def select_snapshot(
        self,
        model_id: str,
        path: str | Path,
        *,
        revision: str | None = None,
    ) -> LocalModelStatus:
        """Select an already-present snapshot without claiming that it is trusted."""

        self._manifest(model_id)
        snapshot = Path(path).expanduser().resolve()
        if not snapshot.is_dir():
            raise FileNotFoundError(f"Local model snapshot not found: {snapshot}")
        resolved_revision = str(revision or snapshot.name).strip()
        if not resolved_revision:
            raise ValueError("Model revision is required when selecting a snapshot.")
        self._select_snapshot(model_id, snapshot, resolved_revision)
        return self.status(model_id)

    def _forget_model(self, model_id: str) -> None:
        state = self._load_state()
        models = state.setdefault("models", {})
        if model_id in models:
            del models[model_id]
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
                    progress(
                        int(self.n),
                        int(self.total) if self.total is not None else None,
                        str(self.desc or "Downloading model"),
                    )
                except Exception:
                    pass

            def update(self, n=1):
                result = super().update(n)
                try:
                    progress(
                        int(self.n),
                        int(self.total) if self.total is not None else None,
                        str(self.desc or "Downloading model"),
                    )
                except Exception:
                    pass
                return result

        return ProductTqdm

    def _download_snapshot(
        self,
        model_id: str,
        *,
        revision: str | None = None,
        force_files: bool = False,
        offline: bool = False,
        progress: ProgressCallback | None = None,
    ) -> tuple[Path, str]:
        manifest = self._manifest(model_id)
        if manifest.provider != ModelProvider.HUGGINGFACE:
            raise RuntimeError(f"Model provider '{manifest.provider.value}' is not supported yet.")
        if offline:
            raise RuntimeError("This model is not installed and Offline mode is on.")

        set_hf_offline(False)
        from huggingface_hub import snapshot_download

        if progress:
            progress(0, None, "Connecting…")
        kwargs = {
            "repo_id": manifest.repo_id,
            "repo_type": "model",
            "revision": revision or manifest.revision_ref,
            "allow_patterns": list(manifest.allow_patterns),
            "token": os.getenv("HF_TOKEN") or None,
            "force_download": bool(force_files),
        }
        tqdm_class = self._tqdm_class(progress)
        if tqdm_class is not None:
            kwargs["tqdm_class"] = tqdm_class

        try:
            local = Path(snapshot_download(**kwargs)).resolve()
        except Exception as exc:
            if "xet" in str(exc).lower() or "hex hash" in str(exc).lower():
                import huggingface_hub.constants as hf_constants

                hf_constants.HF_HUB_DISABLE_XET = True
                local = Path(snapshot_download(**kwargs)).resolve()
            else:
                raise
        resolved_revision = local.name
        if progress:
            progress(1, 1, "Downloaded")
        return local, resolved_revision

    def download(
        self,
        model_id: str,
        *,
        revision: str | None = None,
        refresh: bool = False,
        force_files: bool = False,
        offline: bool = False,
        progress: ProgressCallback | None = None,
    ) -> LocalModelStatus:
        self._manifest(model_id)
        existing = self.status(model_id)
        if existing.installed and not refresh and not force_files and revision is None:
            return existing
        local, resolved_revision = self._download_snapshot(
            model_id,
            revision=revision,
            force_files=force_files,
            offline=offline,
            progress=progress,
        )
        report = self._verify_path(model_id, local, resolved_revision)
        if not report.valid:
            raise RuntimeError(report.warning or "Downloaded model failed verification.")
        self._select_snapshot(model_id, local, resolved_revision)
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
        if status.installed and not status.repairable:
            return status
        if not auto_download:
            if status.repairable:
                raise RuntimeError("This model is incomplete. Open Models to repair it.")
            raise RuntimeError("This model is not installed yet. Open Models to install it.")
        if status.repairable:
            return self.repair(model_id, offline=offline, progress=progress)
        return self.download(model_id, offline=offline, progress=progress)

    def check_update(self, model_id: str, *, offline: bool = False) -> LocalModelStatus:
        current = self.status(model_id)
        if not current.installed or offline:
            return current
        manifest = self._manifest(model_id)
        if manifest.provider != ModelProvider.HUGGINGFACE:
            return current
        set_hf_offline(False)
        try:
            from huggingface_hub import HfApi

            info = HfApi(token=os.getenv("HF_TOKEN") or None).model_info(
                manifest.repo_id,
                revision=manifest.revision_ref,
            )
            remote = str(getattr(info, "sha", "") or "") or None
        except Exception:
            return current
        available = bool(remote and current.revision and remote != current.revision)
        return self._status_from_selected(
            model_id,
            Path(current.snapshot_path) if current.snapshot_path else None,
            current.revision,
            update_available=available,
            remote_revision=remote,
        )

    def stage_update(
        self,
        model_id: str,
        *,
        offline: bool = False,
        progress: ProgressCallback | None = None,
    ) -> LocalModelStatus:
        if offline:
            raise RuntimeError("Turn off Offline mode before checking or downloading model updates.")
        current = self.check_update(model_id, offline=False)
        if not current.installed:
            raise RuntimeError("Install the model before staging an update.")
        if not current.update_available or not current.remote_revision:
            return current

        candidate_path, candidate_revision = self._download_snapshot(
            model_id,
            revision=current.remote_revision,
            force_files=False,
            offline=False,
            progress=progress,
        )
        report = self._verify_path(model_id, candidate_path, candidate_revision)
        if not report.valid or report.source_trusted is not True:
            raise RuntimeError(report.warning or "Candidate model failed trust/verification checks.")

        state = self._load_state()
        entry = state.setdefault("models", {}).setdefault(model_id, {})
        entry["candidate"] = {
            "snapshot_path": str(candidate_path),
            "revision": candidate_revision,
            "staged_at": _utc_now(),
        }
        self._save_state(state)
        return self.status(model_id)

    def promote_candidate(self, model_id: str) -> LocalModelStatus:
        self._manifest(model_id)
        state = self._load_state()
        entry = state.setdefault("models", {}).get(model_id)
        if not isinstance(entry, dict):
            raise RuntimeError("No selected model exists to promote from.")
        candidate = entry.get("candidate")
        if not isinstance(candidate, dict) or not candidate.get("snapshot_path") or not candidate.get("revision"):
            raise RuntimeError("No staged model candidate is available.")

        candidate_path = Path(str(candidate["snapshot_path"])).expanduser().resolve()
        candidate_revision = str(candidate["revision"])
        report = self._verify_path(model_id, candidate_path, candidate_revision)
        if not report.valid or report.source_trusted is not True:
            raise RuntimeError(report.warning or "Candidate model failed trust/verification checks.")

        if entry.get("snapshot_path") and entry.get("revision"):
            entry["previous"] = {
                "snapshot_path": str(entry["snapshot_path"]),
                "revision": str(entry["revision"]),
                "retained_at": _utc_now(),
            }
        entry["snapshot_path"] = str(candidate_path)
        entry["revision"] = candidate_revision
        entry["selected_at"] = _utc_now()
        entry["candidate"] = None
        self._save_state(state)
        return self.status(model_id)

    def rollback(self, model_id: str) -> LocalModelStatus:
        self._manifest(model_id)
        state = self._load_state()
        entry = state.setdefault("models", {}).get(model_id)
        if not isinstance(entry, dict):
            raise RuntimeError("No selected model exists to roll back.")
        previous = entry.get("previous")
        if not isinstance(previous, dict) or not previous.get("snapshot_path") or not previous.get("revision"):
            raise RuntimeError("No previous model revision is available for rollback.")

        previous_path = Path(str(previous["snapshot_path"])).expanduser().resolve()
        previous_revision = str(previous["revision"])
        report = self._verify_path(model_id, previous_path, previous_revision)
        if not report.valid:
            raise RuntimeError(report.warning or "Previous model revision is no longer usable.")

        current = {
            "snapshot_path": entry.get("snapshot_path"),
            "revision": entry.get("revision"),
            "retained_at": _utc_now(),
        }
        entry["snapshot_path"] = str(previous_path)
        entry["revision"] = previous_revision
        entry["selected_at"] = _utc_now()
        entry["previous"] = current
        self._save_state(state)
        return self.status(model_id)

    def discard_candidate(self, model_id: str, *, delete_cache: bool = False) -> bool:
        self._manifest(model_id)
        state = self._load_state()
        entry = state.setdefault("models", {}).get(model_id)
        if not isinstance(entry, dict) or not isinstance(entry.get("candidate"), dict):
            return False
        candidate = dict(entry["candidate"])
        entry["candidate"] = None
        self._save_state(state)

        if delete_cache and candidate.get("snapshot_path") and candidate.get("revision"):
            path = Path(str(candidate["snapshot_path"])).expanduser().resolve()
            revision = str(candidate["revision"])
            manifest = self._manifest(model_id)
            protected = {
                str(entry.get("snapshot_path") or ""),
                str((entry.get("previous") or {}).get("snapshot_path") or "")
                if isinstance(entry.get("previous"), dict)
                else "",
            }
            if str(path) not in protected and self._is_managed_snapshot(manifest, path, revision):
                self._delete_managed_revision(manifest, path, revision)
        return True

    def repair(
        self,
        model_id: str,
        *,
        offline: bool = False,
        progress: ProgressCallback | None = None,
    ) -> LocalModelStatus:
        current = self.status(model_id)
        if offline:
            raise RuntimeError("Turn off Offline mode before repairing model files.")
        # Prefer repairing the exact pinned revision. If the selection is external or
        # has no immutable revision, return to the official manifest ref explicitly.
        manifest = self._manifest(model_id)
        revision = current.revision if current.source_trusted else manifest.revision_ref
        local, resolved_revision = self._download_snapshot(
            model_id,
            revision=revision,
            force_files=True,
            offline=False,
            progress=progress,
        )
        report = self._verify_path(model_id, local, resolved_revision)
        if not report.valid or report.source_trusted is not True:
            raise RuntimeError(report.warning or "Repaired model failed verification.")
        self._select_snapshot(model_id, local, resolved_revision)
        return self.status(model_id)

    def update(
        self,
        model_id: str,
        *,
        offline: bool = False,
        progress: ProgressCallback | None = None,
    ) -> LocalModelStatus:
        """Backward-compatible one-click update built on the safer candidate lifecycle."""

        staged = self.stage_update(model_id, offline=offline, progress=progress)
        if staged.candidate_revision:
            return self.promote_candidate(model_id)
        return staged

    @staticmethod
    def _safe_fallback_delete_snapshot(cache: Path, snapshot: Path, revision: str) -> bool:
        snapshots_root = (cache / "snapshots").resolve()
        try:
            resolved_snapshot = snapshot.resolve()
            resolved_snapshot.relative_to(snapshots_root)
        except (OSError, ValueError) as exc:
            raise RuntimeError("Refusing to remove an unexpected model snapshot path.") from exc
        if resolved_snapshot.parent != snapshots_root or resolved_snapshot.name != revision:
            raise RuntimeError("Refusing to remove a snapshot whose identity does not match the managed revision.")

        removed = False
        if resolved_snapshot.exists():
            shutil.rmtree(resolved_snapshot)
            removed = True

        refs = cache / "refs"
        if refs.exists():
            for ref in refs.rglob("*"):
                if not ref.is_file():
                    continue
                try:
                    if ref.read_text(encoding="utf-8").strip() == revision:
                        ref.unlink()
                except OSError:
                    continue
        return removed

    def _delete_managed_revision(self, manifest: ModelAssetManifest, snapshot: Path, revision: str) -> bool:
        if not self._is_managed_snapshot(manifest, snapshot, revision):
            return False
        cache = self._cache_dir(manifest).resolve()
        root = hf_hub_dir().resolve()
        removed = False
        try:
            from huggingface_hub import scan_cache_dir

            strategy = scan_cache_dir(cache_dir=root).delete_revisions(revision)
            strategy.execute()
            removed = not snapshot.exists()
        except ImportError:
            removed = self._safe_fallback_delete_snapshot(cache, snapshot, revision)
        except Exception as exc:
            try:
                removed = self._safe_fallback_delete_snapshot(cache, snapshot, revision)
            except Exception:
                raise RuntimeError(f"Could not remove the selected model safely: {exc}") from exc
        return removed

    def remove(self, model_id: str) -> bool:
        """Forget the Studio selection and delete only an exact managed revision.

        User-owned/manual folders are never deleted. Candidate/previous revisions are
        not silently removed as a side effect of removing the selected model.
        """

        try:
            manifest = self._manifest(model_id)
        except ValueError:
            return False
        status = self.status(model_id)
        if not status.installed or not status.snapshot_path or not status.revision:
            self._forget_model(model_id)
            return False

        snapshot = Path(status.snapshot_path).expanduser().resolve()
        trusted = self._is_managed_snapshot(manifest, snapshot, status.revision)
        removed = False
        if trusted:
            removed = self._delete_managed_revision(manifest, snapshot, status.revision)

        self._forget_model(model_id)
        return removed
