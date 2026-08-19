from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .engine_assets import (
    RUNTIME_MANIFESTS,
    RuntimeInstallMode,
    RuntimeKind,
    RuntimeManifest,
)


RUNTIME_STATE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    runtime_id: str
    configured: bool
    installed: bool
    ready: bool
    install_mode: str
    environment_path: str | None = None
    python_path: str | None = None
    installed_at: str | None = None
    manifest_fingerprint: str | None = None
    current_fingerprint: str | None = None
    source_revision: str | None = None
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeInstallPlan:
    runtime_id: str
    install_mode: str
    environment_path: str | None
    requirements: tuple[str, ...]
    tool: str | None
    can_install: bool
    reason: str
    bootstrap_requirements: tuple[str, ...] = ()
    bootstrap_index_url: str | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_fingerprint(manifest: RuntimeManifest) -> str:
    payload = json.dumps(
        {
            "runtime_id": manifest.runtime_id,
            "kind": manifest.kind.value,
            "install_mode": manifest.install_mode.value,
            "python_spec": manifest.python_spec,
            "requirements": list(manifest.requirements),
            "bootstrap_requirements": list(manifest.bootstrap_requirements),
            "bootstrap_index_url": manifest.bootstrap_index_url,
            "distribution_name": manifest.distribution_name,
            "source_revision": manifest.source_revision,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _venv_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


class RuntimeManager:
    """Local runtime lifecycle independent from model assets.

    New engine families may carry mutually incompatible Python stacks. The manager keeps
    those environments under one app-owned root and records the exact manifest
    fingerprint used to create them. Platform bootstrap packages may be installed from
    an explicit upstream index before the main engine requirements; this prevents a
    lightweight CPU route from accidentally resolving multi-gigabyte GPU dependencies.
    """

    def __init__(self, root: str | Path, state_path: str | Path | None = None):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = Path(state_path).expanduser().resolve() if state_path else self.root / "runtime_state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _manifest(runtime_id: str) -> RuntimeManifest:
        try:
            return RUNTIME_MANIFESTS[runtime_id]
        except KeyError as exc:
            raise ValueError(f"Unknown runtime '{runtime_id}'.") from exc

    def environment_path(self, runtime_id: str) -> Path:
        self._manifest(runtime_id)
        return self.root / runtime_id

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return {"schema_version": RUNTIME_STATE_SCHEMA_VERSION, "runtimes": {}}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": RUNTIME_STATE_SCHEMA_VERSION, "runtimes": {}}
        if not isinstance(payload, dict):
            return {"schema_version": RUNTIME_STATE_SCHEMA_VERSION, "runtimes": {}}
        version = int(payload.get("schema_version", 1) or 1)
        if version != RUNTIME_STATE_SCHEMA_VERSION:
            raise ValueError(f"Unsupported runtime state schema version: {version}.")
        payload.setdefault("runtimes", {})
        return payload

    def _save_state(self, payload: dict) -> None:
        payload["schema_version"] = RUNTIME_STATE_SCHEMA_VERSION
        payload.setdefault("runtimes", {})
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_path)

    @staticmethod
    def _host_distribution_installed(manifest: RuntimeManifest) -> bool:
        if not manifest.distribution_name:
            return False
        try:
            importlib.metadata.version(manifest.distribution_name)
            return True
        except importlib.metadata.PackageNotFoundError:
            return False

    @staticmethod
    def _uv_executable() -> str | None:
        configured = os.getenv("VOICE_STUDIO_UV")
        if configured:
            path = Path(configured).expanduser()
            if path.is_file():
                return str(path.resolve())
        return shutil.which("uv")

    def status(self, runtime_id: str) -> RuntimeStatus:
        manifest = self._manifest(runtime_id)
        fingerprint = _runtime_fingerprint(manifest)
        if manifest.install_mode == RuntimeInstallMode.HOST_LEGACY:
            installed = self._host_distribution_installed(manifest)
            return RuntimeStatus(
                runtime_id=runtime_id,
                configured=bool(manifest.requirements and manifest.distribution_name),
                installed=installed,
                ready=installed,
                install_mode=manifest.install_mode.value,
                python_path=sys.executable if installed else None,
                current_fingerprint=fingerprint,
                source_revision=manifest.source_revision,
                warning=None if installed else "The legacy host runtime is not installed.",
            )

        environment = self.environment_path(runtime_id)
        python_path = _venv_python(environment)
        state = self._load_state()
        entry = dict((state.get("runtimes") or {}).get(runtime_id) or {})
        recorded = str(entry.get("manifest_fingerprint") or "") or None
        configured = bool(manifest.requirements)
        installed = python_path.is_file()
        ready = bool(configured and installed and recorded == fingerprint)
        warning = None
        if not configured:
            warning = "Runtime is catalogued but its audited install requirements are not defined yet."
        elif installed and recorded != fingerprint:
            warning = "Runtime manifest changed after installation; reinstall or repair this runtime."
        elif not installed:
            warning = "Runtime is not installed."
        return RuntimeStatus(
            runtime_id=runtime_id,
            configured=configured,
            installed=installed,
            ready=ready,
            install_mode=manifest.install_mode.value,
            environment_path=str(environment),
            python_path=str(python_path) if installed else None,
            installed_at=str(entry.get("installed_at")) if entry.get("installed_at") else None,
            manifest_fingerprint=recorded,
            current_fingerprint=fingerprint,
            source_revision=manifest.source_revision,
            warning=warning,
        )

    def statuses(self) -> tuple[RuntimeStatus, ...]:
        return tuple(self.status(runtime_id) for runtime_id in RUNTIME_MANIFESTS)

    def plan_install(self, runtime_id: str) -> RuntimeInstallPlan:
        manifest = self._manifest(runtime_id)
        if manifest.install_mode == RuntimeInstallMode.HOST_LEGACY:
            return RuntimeInstallPlan(
                runtime_id=runtime_id,
                install_mode=manifest.install_mode.value,
                environment_path=None,
                requirements=manifest.requirements,
                tool=None,
                can_install=False,
                reason="This runtime is still installed by the current product setup for compatibility; RuntimeManager does not mutate the host environment.",
                bootstrap_requirements=manifest.bootstrap_requirements,
                bootstrap_index_url=manifest.bootstrap_index_url,
            )
        if manifest.kind != RuntimeKind.PYTHON:
            return RuntimeInstallPlan(
                runtime_id,
                manifest.install_mode.value,
                str(self.environment_path(runtime_id)),
                manifest.requirements,
                None,
                False,
                "Unsupported runtime kind.",
                manifest.bootstrap_requirements,
                manifest.bootstrap_index_url,
            )
        if not manifest.requirements:
            return RuntimeInstallPlan(
                runtime_id=runtime_id,
                install_mode=manifest.install_mode.value,
                environment_path=str(self.environment_path(runtime_id)),
                requirements=(),
                tool=None,
                can_install=False,
                reason="Exact runtime requirements have not been audited/frozen yet.",
                bootstrap_requirements=manifest.bootstrap_requirements,
                bootstrap_index_url=manifest.bootstrap_index_url,
            )
        uv = self._uv_executable()
        return RuntimeInstallPlan(
            runtime_id=runtime_id,
            install_mode=manifest.install_mode.value,
            environment_path=str(self.environment_path(runtime_id)),
            requirements=manifest.requirements,
            tool="uv" if uv else "venv+pip",
            can_install=True,
            reason="Ready for isolated installation.",
            bootstrap_requirements=manifest.bootstrap_requirements,
            bootstrap_index_url=manifest.bootstrap_index_url,
        )

    def _record_install(self, runtime_id: str, tool: str) -> None:
        manifest = self._manifest(runtime_id)
        state = self._load_state()
        state.setdefault("runtimes", {})[runtime_id] = {
            "manifest_fingerprint": _runtime_fingerprint(manifest),
            "installed_at": _utc_now(),
            "source_revision": manifest.source_revision,
            "tool": tool,
        }
        self._save_state(state)

    @staticmethod
    def _install_packages(
        env_python: Path,
        packages: tuple[str, ...],
        *,
        uv: str | None,
        index_url: str | None = None,
    ) -> None:
        if not packages:
            return
        if uv:
            command = [uv, "pip", "install", "--python", str(env_python)]
        else:
            command = [str(env_python), "-m", "pip", "install"]
        if index_url:
            command.extend(["--index-url", index_url])
        command.extend(packages)
        subprocess.run(command, check=True)

    def install(self, runtime_id: str, *, python_executable: str | None = None) -> RuntimeStatus:
        manifest = self._manifest(runtime_id)
        plan = self.plan_install(runtime_id)
        if not plan.can_install:
            raise RuntimeError(plan.reason)
        environment = self.environment_path(runtime_id)
        if environment.exists():
            raise RuntimeError("Runtime directory already exists. Use repair() to recreate it safely.")
        python = python_executable or sys.executable
        uv = self._uv_executable()
        tool = "uv" if uv else "venv+pip"
        try:
            if uv:
                subprocess.run([uv, "venv", str(environment), "--python", python], check=True)
            else:
                subprocess.run([python, "-m", "venv", str(environment)], check=True)
            env_python = _venv_python(environment)
            self._install_packages(
                env_python,
                manifest.bootstrap_requirements,
                uv=uv,
                index_url=manifest.bootstrap_index_url,
            )
            self._install_packages(env_python, manifest.requirements, uv=uv)
        except Exception:
            shutil.rmtree(environment, ignore_errors=True)
            raise
        self._record_install(runtime_id, tool)
        status = self.status(runtime_id)
        if not status.ready:
            raise RuntimeError(status.warning or "Runtime installation did not become ready.")
        return status

    def remove(self, runtime_id: str) -> bool:
        manifest = self._manifest(runtime_id)
        if manifest.install_mode == RuntimeInstallMode.HOST_LEGACY:
            raise RuntimeError("Refusing to remove a host runtime through RuntimeManager.")
        environment = self.environment_path(runtime_id)
        removed = environment.exists()
        if removed:
            shutil.rmtree(environment)
        state = self._load_state()
        state.setdefault("runtimes", {}).pop(runtime_id, None)
        self._save_state(state)
        return removed

    def repair(self, runtime_id: str, *, python_executable: str | None = None) -> RuntimeStatus:
        manifest = self._manifest(runtime_id)
        if manifest.install_mode == RuntimeInstallMode.HOST_LEGACY:
            raise RuntimeError("Repair the current host runtime with the existing product setup; isolated migration has not been activated yet.")
        self.remove(runtime_id)
        return self.install(runtime_id, python_executable=python_executable)
