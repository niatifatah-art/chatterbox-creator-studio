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
