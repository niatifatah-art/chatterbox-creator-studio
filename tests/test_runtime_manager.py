from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from studio.engine_assets import RuntimeInstallMode, RuntimeManifest
from studio.runtime_manager import RuntimeManager, _venv_python


def _isolated(runtime_id: str = "test-runtime", *, requirements=("demo-package==1.0",)) -> RuntimeManifest:
    return RuntimeManifest(
        runtime_id=runtime_id,
        display_name="Test Runtime",
        install_mode=RuntimeInstallMode.ISOLATED,
        requirements=tuple(requirements),
        code_license="MIT",
    )


def test_catalogued_runtime_without_audited_requirements_is_not_installable(tmp_path, monkeypatch):
    manifest = _isolated(requirements=())
    monkeypatch.setitem(__import__("studio.runtime_manager", fromlist=["RUNTIME_MANIFESTS"]).RUNTIME_MANIFESTS, manifest.runtime_id, manifest)
    manager = RuntimeManager(tmp_path / "runtimes")

    plan = manager.plan_install(manifest.runtime_id)
    status = manager.status(manifest.runtime_id)

    assert plan.can_install is False
    assert "not been audited" in plan.reason
    assert status.configured is False
    assert status.ready is False


def test_host_runtime_uses_distribution_metadata_without_importing_engine(tmp_path, monkeypatch):
    module = __import__("studio.runtime_manager", fromlist=["RUNTIME_MANIFESTS"])
    manifest = RuntimeManifest(
        runtime_id="host-test",
        display_name="Host Test",
        install_mode=RuntimeInstallMode.HOST_LEGACY,
        requirements=("demo-dist==1.0",),
        distribution_name="demo-dist",
    )
    monkeypatch.setitem(module.RUNTIME_MANIFESTS, manifest.runtime_id, manifest)

    calls: list[str] = []

    def fake_version(name: str) -> str:
        calls.append(name)
        return "1.0"

    monkeypatch.setattr(module.importlib.metadata, "version", fake_version)
    status = RuntimeManager(tmp_path / "runtimes").status(manifest.runtime_id)

    assert calls == ["demo-dist"]
    assert status.configured is True
    assert status.installed is True
    assert status.ready is True
    assert status.environment_path is None


def test_isolated_runtime_is_ready_only_when_environment_and_manifest_fingerprint_match(tmp_path, monkeypatch):
    module = __import__("studio.runtime_manager", fromlist=["RUNTIME_MANIFESTS"])
    manifest = _isolated()
    monkeypatch.setitem(module.RUNTIME_MANIFESTS, manifest.runtime_id, manifest)
    manager = RuntimeManager(tmp_path / "runtimes")
    environment = manager.environment_path(manifest.runtime_id)
    python_path = _venv_python(environment)
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.write_bytes(b"fake-python")
    manager._record_install(manifest.runtime_id, "test")

    ready = manager.status(manifest.runtime_id)
    assert ready.installed is True
    assert ready.ready is True

    changed = RuntimeManifest(
        runtime_id=manifest.runtime_id,
        display_name=manifest.display_name,
        install_mode=RuntimeInstallMode.ISOLATED,
        requirements=("demo-package==2.0",),
    )
    monkeypatch.setitem(module.RUNTIME_MANIFESTS, manifest.runtime_id, changed)
    stale = manager.status(manifest.runtime_id)
    assert stale.installed is True
    assert stale.ready is False
    assert "manifest changed" in (stale.warning or "").lower()


def test_manifest_bootstrap_changes_runtime_fingerprint(tmp_path, monkeypatch):
    module = __import__("studio.runtime_manager", fromlist=["RUNTIME_MANIFESTS"])
    manifest = RuntimeManifest(
        runtime_id="bootstrap-test",
        display_name="Bootstrap Test",
        install_mode=RuntimeInstallMode.ISOLATED,
        requirements=("demo-package==1.0",),
        bootstrap_requirements=("torch==1.0",),
        bootstrap_index_url="https://packages.example.invalid/cpu",
    )
    monkeypatch.setitem(module.RUNTIME_MANIFESTS, manifest.runtime_id, manifest)
    manager = RuntimeManager(tmp_path / "runtimes")
    environment = manager.environment_path(manifest.runtime_id)
    python_path = _venv_python(environment)
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.write_bytes(b"fake")
    manager._record_install(manifest.runtime_id, "test")
    assert manager.status(manifest.runtime_id).ready is True

    changed = RuntimeManifest(
        runtime_id=manifest.runtime_id,
        display_name=manifest.display_name,
        install_mode=RuntimeInstallMode.ISOLATED,
        requirements=manifest.requirements,
        bootstrap_requirements=("torch==2.0",),
        bootstrap_index_url=manifest.bootstrap_index_url,
    )
    monkeypatch.setitem(module.RUNTIME_MANIFESTS, manifest.runtime_id, changed)
    assert manager.status(manifest.runtime_id).ready is False


def test_install_bootstraps_from_explicit_index_before_main_requirements(tmp_path, monkeypatch):
    module = __import__("studio.runtime_manager", fromlist=["RUNTIME_MANIFESTS"])
    manifest = RuntimeManifest(
        runtime_id="bootstrap-order",
        display_name="Bootstrap Order",
        install_mode=RuntimeInstallMode.ISOLATED,
        requirements=("engine-package==1.0",),
        bootstrap_requirements=("torch==2.13.0",),
        bootstrap_index_url="https://download.example.invalid/cpu",
    )
    monkeypatch.setitem(module.RUNTIME_MANIFESTS, manifest.runtime_id, manifest)
    manager = RuntimeManager(tmp_path / "runtimes")
    monkeypatch.setattr(manager, "_uv_executable", lambda: None)
    calls: list[list[str]] = []

    def fake_run(command, check):
        command = [str(part) for part in command]
        calls.append(command)
        if len(calls) == 1:
            environment = Path(command[-1])
            python_path = _venv_python(environment)
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.write_bytes(b"fake-python")
        return None

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    status = manager.install(manifest.runtime_id)

    assert status.ready is True
    assert len(calls) == 3
    assert calls[1][-1] == "torch==2.13.0"
    assert "--index-url" in calls[1]
    assert "https://download.example.invalid/cpu" in calls[1]
    assert calls[2][-1] == "engine-package==1.0"
    assert "--index-url" not in calls[2]


def test_failed_isolated_install_removes_partial_environment(tmp_path, monkeypatch):
    module = __import__("studio.runtime_manager", fromlist=["RUNTIME_MANIFESTS"])
    manifest = _isolated()
    monkeypatch.setitem(module.RUNTIME_MANIFESTS, manifest.runtime_id, manifest)
    manager = RuntimeManager(tmp_path / "runtimes")
    monkeypatch.setattr(manager, "_uv_executable", lambda: None)
    calls = 0

    def fake_run(command, check):
        nonlocal calls
        calls += 1
        if calls == 1:
            environment = Path(command[-1])
            python_path = _venv_python(environment)
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.write_bytes(b"partial")
            return None
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        manager.install(manifest.runtime_id)
    assert not manager.environment_path(manifest.runtime_id).exists()


def test_remove_deletes_only_app_owned_isolated_environment(tmp_path, monkeypatch):
    module = __import__("studio.runtime_manager", fromlist=["RUNTIME_MANIFESTS"])
    manifest = _isolated()
    monkeypatch.setitem(module.RUNTIME_MANIFESTS, manifest.runtime_id, manifest)
    manager = RuntimeManager(tmp_path / "runtimes")
    environment = manager.environment_path(manifest.runtime_id)
    python_path = _venv_python(environment)
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.write_bytes(b"fake")
    manager._record_install(manifest.runtime_id, "test")

    assert manager.remove(manifest.runtime_id) is True
    assert not environment.exists()
    assert manager.status(manifest.runtime_id).installed is False


def test_runtime_manager_refuses_to_remove_host_legacy_runtime(tmp_path):
    manager = RuntimeManager(tmp_path / "runtimes")
    with pytest.raises(RuntimeError, match="host runtime"):
        manager.remove("chatterbox")
