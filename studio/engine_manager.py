from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .engine_registry import ENGINE_MANIFESTS, EngineManifest
from .model_manager import LocalModelManager, LocalModelStatus, ModelVerificationReport
from .protocol import EngineStatus
from .runtime_manager import RuntimeInstallPlan, RuntimeManager, RuntimeStatus


@dataclass(frozen=True, slots=True)
class EngineReadiness:
    engine_id: str
    display_name: str
    catalogue_status: str
    runtime: RuntimeStatus | None
    models: tuple[LocalModelStatus, ...]
    ready: bool
    auto_eligible: bool
    reasons: tuple[str, ...]


class EngineManager:
    """Product-facing lifecycle service for engine/runtime/model readiness.

    This class deliberately does not execute speech. It answers the management
    questions the UI and diagnostics need: what is catalogued, what runtime is ready,
    what model revision is selected, whether it verifies, and which recovery/update
    action is available. Network-changing operations remain explicit methods.
    """

    def __init__(
        self,
        data_dir: str | Path,
        *,
        runtime_manager: RuntimeManager | None = None,
        model_manager: LocalModelManager | None = None,
    ):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_manager = runtime_manager or RuntimeManager(self.data_dir / "runtimes")
        self.model_manager = model_manager or LocalModelManager(self.data_dir / "model_state.json")

    @staticmethod
    def _manifest(engine_id: str) -> EngineManifest:
        try:
            return ENGINE_MANIFESTS[engine_id]
        except KeyError as exc:
            raise ValueError(f"Unknown engine '{engine_id}'.") from exc

    def status(self, engine_id: str) -> EngineReadiness:
        manifest = self._manifest(engine_id)
        runtime: RuntimeStatus | None
        try:
            runtime = self.runtime_manager.status(manifest.runtime_id) if manifest.runtime_id else None
        except ValueError:
            runtime = None

        models: list[LocalModelStatus] = []
        reasons: list[str] = []
        for model_id in manifest.model_ids:
            try:
                model = self.model_manager.status(model_id)
            except ValueError:
                reasons.append(f"Model asset '{model_id}' is not configured.")
                continue
            models.append(model)
            if not model.installed:
                reasons.append(f"Model '{model_id}' is not installed.")
            elif model.repairable:
                reasons.append(f"Model '{model_id}' failed verification and needs repair.")
            elif model.source_trusted is False:
                reasons.append(f"Model '{model_id}' is outside the trusted managed cache.")

        if runtime is None:
            reasons.append("Runtime is not configured for this engine yet.")
        elif not runtime.configured:
            reasons.append(runtime.warning or "Runtime install requirements are not configured yet.")
        elif not runtime.ready:
            reasons.append(runtime.warning or "Runtime is not ready.")

        if not manifest.model_ids:
            reasons.append("No model asset has been selected for this engine yet.")

        ready = bool(
            runtime is not None
            and runtime.ready
            and manifest.model_ids
            and len(models) == len(manifest.model_ids)
            and all(
                model.installed and not model.repairable and model.source_trusted is not False
                for model in models
            )
        )
        if ready and manifest.status != EngineStatus.SUPPORTED:
            reasons.append(
                "Engine is installed and runnable but remains catalogued until certification promotes it to Auto."
            )
        auto_eligible = bool(ready and manifest.status == EngineStatus.SUPPORTED)
        return EngineReadiness(
            engine_id=manifest.engine_id,
            display_name=manifest.display_name,
            catalogue_status=manifest.status.value,
            runtime=runtime,
            models=tuple(models),
            ready=ready,
            auto_eligible=auto_eligible,
            reasons=tuple(dict.fromkeys(reasons)),
        )

    def statuses(self, *, include_catalogued: bool = True) -> tuple[EngineReadiness, ...]:
        rows: list[EngineReadiness] = []
        for manifest in sorted(ENGINE_MANIFESTS.values(), key=lambda item: item.engine_id):
            if not include_catalogued and manifest.status != EngineStatus.SUPPORTED:
                continue
            rows.append(self.status(manifest.engine_id))
        return tuple(rows)

    def runtime_install_plan(self, engine_id: str) -> RuntimeInstallPlan:
        manifest = self._manifest(engine_id)
        if not manifest.runtime_id:
            raise RuntimeError("This engine has no runtime manifest yet.")
        return self.runtime_manager.plan_install(manifest.runtime_id)

    def install_runtime(self, engine_id: str) -> RuntimeStatus:
        manifest = self._manifest(engine_id)
        if not manifest.runtime_id:
            raise RuntimeError("This engine has no runtime manifest yet.")
        return self.runtime_manager.install(manifest.runtime_id)

    def repair_runtime(self, engine_id: str) -> RuntimeStatus:
        manifest = self._manifest(engine_id)
        if not manifest.runtime_id:
            raise RuntimeError("This engine has no runtime manifest yet.")
        return self.runtime_manager.repair(manifest.runtime_id)

    def remove_runtime(self, engine_id: str) -> bool:
        manifest = self._manifest(engine_id)
        if not manifest.runtime_id:
            return False
        return self.runtime_manager.remove(manifest.runtime_id)

    def install_model(self, model_id: str, *, offline: bool = False, progress=None) -> LocalModelStatus:
        return self.model_manager.download(model_id, offline=offline, progress=progress)

    def verify_model(self, model_id: str) -> ModelVerificationReport:
        return self.model_manager.verify(model_id)

    def repair_model(self, model_id: str, *, offline: bool = False, progress=None) -> LocalModelStatus:
        return self.model_manager.repair(model_id, offline=offline, progress=progress)

    def check_model_update(self, model_id: str, *, offline: bool = False) -> LocalModelStatus:
        return self.model_manager.check_update(model_id, offline=offline)

    def stage_model_update(self, model_id: str, *, offline: bool = False, progress=None) -> LocalModelStatus:
        return self.model_manager.stage_update(model_id, offline=offline, progress=progress)

    def promote_model_candidate(self, model_id: str) -> LocalModelStatus:
        return self.model_manager.promote_candidate(model_id)

    def rollback_model(self, model_id: str) -> LocalModelStatus:
        return self.model_manager.rollback(model_id)

    def discard_model_candidate(self, model_id: str, *, delete_cache: bool = False) -> bool:
        return self.model_manager.discard_candidate(model_id, delete_cache=delete_cache)

    def remove_model(self, model_id: str) -> bool:
        return self.model_manager.remove(model_id)
