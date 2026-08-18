from __future__ import annotations

from pathlib import Path

from studio.engine_manager import EngineManager
from studio.model_manager import LocalModelStatus
from studio.runtime_manager import RuntimeInstallPlan, RuntimeStatus


class FakeRuntimeManager:
    def __init__(self, ready_ids=()):
        self.ready_ids = set(ready_ids)
        self.install_called = False

    def status(self, runtime_id: str) -> RuntimeStatus:
        ready = runtime_id in self.ready_ids
        return RuntimeStatus(
            runtime_id=runtime_id,
            configured=ready,
            installed=ready,
            ready=ready,
            install_mode="isolated",
            warning=None if ready else "Runtime is not configured.",
        )

    def plan_install(self, runtime_id: str) -> RuntimeInstallPlan:
        return RuntimeInstallPlan(runtime_id, "isolated", None, (), None, False, "not configured")

    def install(self, runtime_id: str):
        self.install_called = True
        raise AssertionError("status must never install a runtime")


class FakeModelManager:
    def __init__(self, states: dict[str, LocalModelStatus]):
        self.states = states
        self.download_called = False

    def status(self, model_id: str) -> LocalModelStatus:
        return self.states[model_id]

    def download(self, *args, **kwargs):
        self.download_called = True
        raise AssertionError("status must never download a model")


def _model(model_id: str, *, installed=True, trusted=True, repairable=False) -> LocalModelStatus:
    return LocalModelStatus(
        model_id=model_id,
        installed=installed,
        snapshot_path=f"/managed/{model_id}" if installed else None,
        revision="rev" if installed else None,
        size_gb=1.0 if installed else 0.0,
        source_trusted=trusted if installed else None,
        repairable=repairable,
    )


def test_supported_engine_is_auto_eligible_only_when_runtime_and_model_are_ready(tmp_path: Path):
    runtimes = FakeRuntimeManager({"chatterbox"})
    models = FakeModelManager({"nano": _model("nano")})
    manager = EngineManager(tmp_path, runtime_manager=runtimes, model_manager=models)

    status = manager.status("chatterbox-nano")

    assert status.ready is True
    assert status.auto_eligible is True
    assert status.reasons == ()
    assert runtimes.install_called is False
    assert models.download_called is False


def test_untrusted_or_broken_model_never_becomes_auto_eligible(tmp_path: Path):
    runtimes = FakeRuntimeManager({"chatterbox"})
    for model in (
        _model("nano", trusted=False),
        _model("nano", repairable=True),
        _model("nano", installed=False),
    ):
        manager = EngineManager(tmp_path, runtime_manager=runtimes, model_manager=FakeModelManager({"nano": model}))
        status = manager.status("chatterbox-nano")
        assert status.ready is False
        assert status.auto_eligible is False
        assert status.reasons


def test_catalogued_engine_without_selected_model_asset_is_not_ready(tmp_path: Path):
    manager = EngineManager(
        tmp_path,
        runtime_manager=FakeRuntimeManager(),
        model_manager=FakeModelManager({}),
    )

    status = manager.status("kokoro")

    assert status.catalogue_status == "catalogued"
    assert status.ready is False
    assert status.auto_eligible is False
    assert any("model asset" in reason.lower() for reason in status.reasons)
