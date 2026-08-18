from __future__ import annotations

import shutil
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from studio.artifact_store import ArtifactStore
from studio.cancellation import GenerationCancelled, raise_if_generation_cancelled
from studio.engine_registry import ENGINE_MANIFESTS, EngineManifest
from studio.language import normalize_language_code
from studio.model_manager import LocalModelManager, LocalModelStatus
from studio.protocol import (
    Capability,
    Provenance,
    SpeechArtifact,
    SpeechErrorKind,
    SpeechSynthesisRequest,
    VoiceSourceKind,
)
from studio.speech_router import RouteError, RouteRequest, route
from studio.voice_profile_store import StoredVoiceProfile, VoiceProfileStore


class SynthesisError(RuntimeError):
    """Core-level synthesis failure with stable recovery semantics."""

    def __init__(self, kind: SpeechErrorKind, message: str, *, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.kind = kind
        self.data = dict(data or {})


@dataclass(frozen=True, slots=True)
class SynthesisExecutionSettings:
    """Internal compatibility settings for the proven Chatterbox path.

    These are deliberately **not** added to the public semantic request. The current
    Gradio UI can pass its exact technical controls during migration, while future
    engines translate semantic style/recipes through their own adapters.
    """

    seed: int | None = None
    device: str | None = None
    device_label: str | None = None
    exaggeration: float = 0.5
    cfg_weight: float = 0.5
    temperature: float = 0.8
    repetition_penalty: float = 1.2
    min_p: float = 0.05
    top_p: float = 1.0
    top_k: int = 1000
    speech_speed: float = 1.0
    raw_mode: bool = False
    smart_chunking: bool = True
    max_chars: int = 280
    chunk_gap_seconds: float = 0.06
    recipe_revision: str | None = None


class ModelManagerProtocol(Protocol):
    def status(self, model_id: str) -> LocalModelStatus: ...


class GenerationResultProtocol(Protocol):
    audio_path: Path
    metadata_path: Path
    model_id: str
    seed: int
    chunk_count: int


class EngineProtocol(Protocol):
    device: str
    device_label: str

    def set_device(self, device: str, label: str | None = None) -> None: ...

    def set_model_path(self, model_id: str, path: str | Path | None) -> None: ...

    def generate(self, **kwargs: Any) -> GenerationResultProtocol: ...


EngineFactory = Callable[[Path], EngineProtocol]
ProgressCallback = Callable[[str, int | None, int | None], None]

# Phase 2 deliberately proves the public Core boundary with the existing Chatterbox
# implementation only. The router may catalogue future engines, but execution must
# never fall through into the wrong adapter. Phase 4 replaces this guard with generic
# engine-family dispatch once isolated runtimes/adapters exist.
PHASE2_EXECUTION_FAMILIES = frozenset({"chatterbox"})


def _default_engine_factory(output_dir: Path) -> EngineProtocol:
    # Import lazily so protocol/discovery tests never import the ML implementation.
    from studio.engine import ChatterboxEngine

    return ChatterboxEngine(output_dir)


def _notify(callback: ProgressCallback | None, stage: str, current: int | None = None, total: int | None = None) -> None:
    raise_if_generation_cancelled()
    if callback is not None:
        callback(stage, current, total)
    raise_if_generation_cancelled()


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
        if rate:
            return frames / float(rate)
    except (wave.Error, EOFError, OSError):
        pass
    # Chatterbox/torchaudio may emit a WAV encoding the stdlib reader cannot inspect.
    # Keep the fallback lazy so model-free tests do not acquire torch/torchaudio.
    try:
        import torchaudio as ta

        info = ta.info(str(path))
        if info.sample_rate:
            return info.num_frames / float(info.sample_rate)
    except Exception as exc:  # pragma: no cover - exercised only by non-PCM backends
        raise SynthesisError(
            SpeechErrorKind.GENERATION_FAILED,
            "Generated audio could not be inspected.",
            data={"error_class": type(exc).__name__},
        ) from exc
    raise SynthesisError(SpeechErrorKind.GENERATION_FAILED, "Generated audio has no valid duration.")


def _safe_generation_metadata(result: GenerationResultProtocol, settings: SynthesisExecutionSettings) -> dict[str, Any]:
    # Keep content/path-bearing legacy metadata private. Public artifact metadata should
    # be useful to downstream automation without leaking scripts or machine locations.
    return {
        "seed": int(result.seed),
        "chunk_count": int(result.chunk_count),
        "raw_mode": bool(settings.raw_mode),
        "smart_chunking": bool(settings.smart_chunking and not settings.raw_mode),
        "speech_speed": float(settings.speech_speed),
    }


class SpeechSynthesisService:
    """Reusable Speech Core synthesis service.

    Phase 2 proves the boundary with the already-supported Chatterbox routes. It does
    not silently download models, does not know about Gradio/ACE accounts, and returns
    only durable logical artifacts.
    """

    def __init__(
        self,
        data_dir: str | Path,
        *,
        profile_store: VoiceProfileStore | None = None,
        artifact_store: ArtifactStore | None = None,
        model_manager: ModelManagerProtocol | None = None,
        engine_factory: EngineFactory | None = None,
    ):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.profile_store = profile_store or VoiceProfileStore(self.data_dir / "voice-profiles")
        self.artifact_store = artifact_store or ArtifactStore(self.data_dir / "artifacts")
        # The existing product keeps model_state.json beside the speech-core directory.
        # This default preserves that layout without making it part of the public API.
        self.model_manager = model_manager or LocalModelManager(self.data_dir.parent / "model_state.json")
        self.engine_factory = engine_factory or _default_engine_factory
        self.work_dir = self.data_dir / "generation-work"
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def _status_for_manifest(
        self,
        manifest: EngineManifest,
        binding_model_id: str | None = None,
    ) -> tuple[str | None, LocalModelStatus | None]:
        model_id = binding_model_id
        if model_id is None and len(manifest.model_ids) == 1:
            model_id = manifest.model_ids[0]
        if model_id is None:
            return None, None
        if manifest.model_ids and model_id not in manifest.model_ids:
            return model_id, None
        try:
            return model_id, self.model_manager.status(model_id)
        except (KeyError, ValueError):
            return model_id, None

    def _installed_synthesis_engines(self, profile: StoredVoiceProfile) -> frozenset[str]:
        rows: set[str] = set()
        for manifest in ENGINE_MANIFESTS.values():
            if Capability.SYNTHESIZE not in manifest.capabilities:
                continue
            binding = profile.binding_for(manifest.engine_id)
            _model_id, status = self._status_for_manifest(manifest, binding.model_id if binding else None)
            if status is not None and status.installed and status.snapshot_path:
                rows.add(manifest.engine_id)
        return frozenset(rows)

    @staticmethod
    def _requires_clone(profile: StoredVoiceProfile) -> bool:
        return profile.profile.source.kind in {VoiceSourceKind.CLONE, VoiceSourceKind.SAVED} and profile.profile.source.reference is not None

    def _resolve_profile(self, request: SpeechSynthesisRequest) -> StoredVoiceProfile:
        if not request.voice_profile_id.strip():
            raise SynthesisError(SpeechErrorKind.INVALID_ARGUMENT, "voice_profile_id is required.")
        record = self.profile_store.get(request.voice_profile_id)
        if record is None:
            raise SynthesisError(
                SpeechErrorKind.NOT_FOUND,
                f"Voice profile '{request.voice_profile_id}' was not found.",
            )
        if request.voice_revision is not None and request.voice_revision != record.profile.revision:
            raise SynthesisError(
                SpeechErrorKind.INVALID_ARGUMENT,
                "Requested voice revision does not match the installed profile revision.",
                data={"requested_revision": request.voice_revision, "current_revision": record.profile.revision},
            )
        return record

    def _resolve_reference(self, profile: StoredVoiceProfile) -> Path:
        source = profile.profile.source
        if source.reference is None:
            raise SynthesisError(
                SpeechErrorKind.UNSUPPORTED_VOICE_SOURCE,
                f"Voice source '{source.kind.value}' does not provide a clone reference for the current Chatterbox route.",
            )
        try:
            return self.artifact_store.resolve(source.reference)
        except (FileNotFoundError, ValueError) as exc:
            raise SynthesisError(
                SpeechErrorKind.VOICE_REFERENCE_MISSING,
                "The saved voice reference is missing or failed its integrity check.",
                data={"artifact_id": source.reference.artifact_id},
            ) from exc

    def _select_route(self, request: SpeechSynthesisRequest, profile: StoredVoiceProfile, language: str):
        installed = self._installed_synthesis_engines(profile)
        preferred = profile.profile.preferred_engine_id if profile.profile.consistency_locked else None
        try:
            return route(
                RouteRequest(
                    capability=Capability.SYNTHESIZE,
                    language=language,
                    priority=request.priority,
                    needs_voice_clone=self._requires_clone(profile),
                    needs_voice_design=profile.profile.source.kind == VoiceSourceKind.DESIGNED,
                    needs_ready_voice=profile.profile.source.kind == VoiceSourceKind.READY,
                    installed_engines=installed,
                    consistency_engine=preferred,
                    engine_override=request.engine_override,
                )
            )
        except RouteError as exc:
            raise SynthesisError(exc.kind, str(exc)) from exc

    def synthesize(
        self,
        request: SpeechSynthesisRequest,
        *,
        execution: SynthesisExecutionSettings | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> SpeechArtifact:
        text = (request.text or "").strip()
        if not text:
            raise SynthesisError(SpeechErrorKind.INVALID_ARGUMENT, "Text is required.")
        if request.events:
            # Semantic event positioning needs one exact cross-engine definition before
            # it is exposed. Existing `[pause=...]` text remains supported through the
            # proven Chatterbox engine during migration; events are never ignored.
            raise SynthesisError(
                SpeechErrorKind.INVALID_ARGUMENT,
                "Structured speech events are not executable in this synthesis phase yet.",
            )

        settings = execution or SynthesisExecutionSettings()
        try:
            _notify(progress_callback, "preparing")
            profile = self._resolve_profile(request)
            try:
                language = normalize_language_code(request.language, text)
            except ValueError as exc:
                raise SynthesisError(SpeechErrorKind.INVALID_ARGUMENT, str(exc)) from exc

            decision = self._select_route(request, profile, language)
            manifest = ENGINE_MANIFESTS[decision.engine_id]
            if manifest.family not in PHASE2_EXECUTION_FAMILIES:
                raise SynthesisError(
                    SpeechErrorKind.ENGINE_UNAVAILABLE,
                    f"{manifest.display_name} is catalogued but does not have a local execution adapter in this Speech Core build yet.",
                    data={"engine_id": manifest.engine_id, "family": manifest.family},
                )
            binding = profile.binding_for(decision.engine_id)
            model_id, status = self._status_for_manifest(manifest, binding.model_id if binding else None)
            if model_id is None:
                raise SynthesisError(
                    SpeechErrorKind.ENGINE_UNAVAILABLE,
                    f"{manifest.display_name} does not have a certified model asset for local synthesis yet.",
                    data={"engine_id": manifest.engine_id},
                )
            if status is None or not status.installed or not status.snapshot_path:
                raise SynthesisError(
                    SpeechErrorKind.MODEL_NOT_INSTALLED,
                    f"The model required by {manifest.display_name} is not installed.",
                    data={"engine_id": manifest.engine_id, "model_id": model_id},
                )

            reference = self._resolve_reference(profile)
            _notify(progress_callback, "loading")

            invocation_dir = self.work_dir / uuid.uuid4().hex
            invocation_dir.mkdir(parents=True, exist_ok=False)
            engine: EngineProtocol | None = None
            try:
                engine = self.engine_factory(invocation_dir)
                if settings.device:
                    engine.set_device(settings.device, settings.device_label)
                engine.set_model_path(model_id, status.snapshot_path)

                def engine_progress(message: str, current: int | None, total: int | None) -> None:
                    _notify(progress_callback, message or "generating", current, total)

                _notify(progress_callback, "generating", 0, None)
                result = engine.generate(
                    script=text,
                    voice_path=reference,
                    model_id=model_id,
                    language_id=language,
                    exaggeration=settings.exaggeration,
                    cfg_weight=settings.cfg_weight,
                    temperature=settings.temperature,
                    repetition_penalty=settings.repetition_penalty,
                    min_p=settings.min_p,
                    top_p=settings.top_p,
                    top_k=settings.top_k,
                    speech_speed=settings.speech_speed,
                    raw_mode=settings.raw_mode,
                    smart_chunking=settings.smart_chunking,
                    max_chars=settings.max_chars,
                    chunk_gap_seconds=settings.chunk_gap_seconds,
                    seed=settings.seed,
                    progress_callback=engine_progress,
                )
                if result.model_id != model_id:
                    raise SynthesisError(
                        SpeechErrorKind.GENERATION_FAILED,
                        "Speech engine generated with an unexpected model identity.",
                        data={"expected_model_id": model_id, "actual_model_id": result.model_id},
                    )
                audio_path = Path(result.audio_path).resolve()
                if not audio_path.is_file():
                    raise SynthesisError(SpeechErrorKind.GENERATION_FAILED, "Speech engine returned no audio file.")
                _notify(progress_callback, "validating")
                duration = _wav_duration(audio_path)
                artifact = self.artifact_store.register_file(
                    audio_path,
                    artifact_id=f"speech-{uuid.uuid4().hex}",
                    mime_type="audio/wav",
                    copy=True,
                )
                recipe_revision = settings.recipe_revision
                if recipe_revision is None and binding is not None and binding.recipe_revision is not None:
                    # Record a binding recipe revision only when it is the current preferred
                    # engine: generic recipe application is implemented in the calibration
                    # phase. This avoids claiming an unapplied candidate recipe.
                    if profile.profile.preferred_engine_id == decision.engine_id:
                        recipe_revision = str(binding.recipe_revision)
                public_metadata = _safe_generation_metadata(result, settings)
                public_metadata["route_reason"] = decision.reason
                public_metadata["source_kind"] = profile.profile.source.kind.value
                public_metadata["style_mapping"] = "pending" if request.style not in {"", "auto"} else "auto"
                if request.pronunciation_hints or profile.pronunciation_hints:
                    public_metadata["pronunciation_hints_applied"] = False
                _notify(progress_callback, "complete", 1, 1)
                return SpeechArtifact(
                    audio=artifact,
                    duration_seconds=duration,
                    language=language,
                    voice_profile_id=profile.profile.profile_id,
                    voice_revision=profile.profile.revision,
                    style=request.style or profile.profile.default_style or "auto",
                    provenance=Provenance(
                        engine_id=decision.engine_id,
                        model_id=model_id,
                        model_revision=(status.revision or binding.model_revision) if binding else status.revision,
                        recipe_revision=recipe_revision,
                    ),
                    metadata=public_metadata,
                )
            finally:
                if engine is not None:
                    try:
                        unload = getattr(engine, "unload", None)
                        if callable(unload):
                            unload()
                    except Exception:
                        pass
                shutil.rmtree(invocation_dir, ignore_errors=True)
        except GenerationCancelled as exc:
            raise SynthesisError(SpeechErrorKind.CANCELLED, str(exc)) from exc
        except SynthesisError:
            raise
        except Exception as exc:
            engine_id = locals().get("decision").engine_id if "decision" in locals() else None
            model = locals().get("model_id")
            raise SynthesisError(
                SpeechErrorKind.GENERATION_FAILED,
                "Speech generation failed.",
                data={"error_class": type(exc).__name__, "engine_id": engine_id, "model_id": model},
            ) from exc
