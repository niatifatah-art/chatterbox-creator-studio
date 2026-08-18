from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from studio.artifact_store import ArtifactStore
from studio.engine import GenerationResult, NativeChatterboxEngine
from studio.engine_registry import ENGINE_MANIFESTS
from studio.model_manager import LocalModelManager
from studio.protocol import Capability, SpeechSynthesisRequest, VoiceSourceKind
from studio.synthesis import SpeechSynthesisService, SynthesisExecutionSettings
from studio.voice_profile_store import VoiceProfileStore
from studio.voices import VoiceLibrary


class CoreGenerationEngine:
    """Temporary controller facade backed by Speech Core.

    Existing UI/CLI/reliability code still calls an engine-like object. This facade
    keeps that call shape while routing generation through `SpeechSynthesisService`.
    Saved legacy WAV paths are resolved back to their canonical Voice Profile. An
    arbitrary CLI WAV gets a short-lived profile/artifact and is cleaned afterwards.

    New engine families must never be added here. Generic engine/runtime dispatch is a
    Phase 4 Speech Core responsibility; this compatibility facade disappears with the
    legacy controller shell.
    """

    def __init__(
        self,
        output_dir: str | Path,
        core_dir: str | Path | None = None,
        *,
        model_manager: LocalModelManager | None = None,
        profile_store: VoiceProfileStore | None = None,
        artifact_store: ArtifactStore | None = None,
    ):
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        storage_root = self.output_dir.parent
        data_dir = storage_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        self.core_dir = Path(core_dir).expanduser().resolve() if core_dir is not None else (data_dir / "speech-core").resolve()
        self.core_dir.mkdir(parents=True, exist_ok=True)
        self.model_manager = model_manager or LocalModelManager(data_dir / "model_state.json")
        self.profile_store = profile_store or VoiceProfileStore(self.core_dir / "voice-profiles")
        self.artifact_store = artifact_store or ArtifactStore(self.core_dir / "artifacts")
        # The facade is allowed to read the temporary legacy mirror only to map old
        # controller arguments back to canonical profile IDs. Identity still lives in
        # VoiceProfileStore.
        self.voice_library = VoiceLibrary(data_dir / "voices", core_directory=self.core_dir)

        # One process-owned native engine preserves current loaded-model behavior for
        # Best-of, retries, Batch and Compare. Speech Core still owns route/reference /
        # revision checks and the call into this native implementation.
        self._engine = NativeChatterboxEngine(self.core_dir / "generation-work")

        def factory(invocation_dir: Path):
            invocation_dir.mkdir(parents=True, exist_ok=True)
            self._engine.output_dir = invocation_dir
            return self._engine

        self.service = SpeechSynthesisService(
            self.core_dir,
            profile_store=self.profile_store,
            artifact_store=self.artifact_store,
            model_manager=self.model_manager,
            engine_factory=factory,
            release_engine_after_request=False,
        )

    @property
    def device(self) -> str:
        return self._engine.device

    @property
    def device_label(self) -> str:
        return self._engine.device_label

    @property
    def loaded(self) -> bool:
        return self._engine.loaded

    @property
    def loaded_model_id(self) -> str | None:
        return self._engine.loaded_model_id

    def set_device(self, device: str, label: str | None = None) -> None:
        self._engine.set_device(device, label)

    def set_model_path(self, model_id: str, path: str | Path | None) -> None:
        self._engine.set_model_path(model_id, path)

    def load_model(self, model_id: str, progress_callback=None) -> None:
        self._engine.load_model(model_id, progress_callback=progress_callback)

    def unload(self) -> None:
        self._engine.unload()

    @staticmethod
    def _engine_id_for_model(model_id: str) -> str:
        matches = [
            manifest.engine_id
            for manifest in ENGINE_MANIFESTS.values()
            if manifest.family == "chatterbox"
            and Capability.SYNTHESIZE in manifest.capabilities
            and model_id in manifest.model_ids
        ]
        if len(matches) != 1:
            raise ValueError(f"No unique Speech Core Chatterbox route for model '{model_id}'.")
        return matches[0]

    @staticmethod
    def _copy_private_result(result, output_dir: Path) -> GenerationResult:
        source_audio = Path(result.audio_path)
        source_metadata = Path(result.metadata_path)
        if not source_audio.is_file() or not source_metadata.is_file():
            raise RuntimeError("Speech Core engine result disappeared before UI compatibility export.")

        audio = output_dir / source_audio.name
        metadata = output_dir / source_metadata.name
        if audio.exists() or metadata.exists():
            stem = f"{source_audio.stem}-{result.seed}"
            audio = output_dir / f"{stem}.wav"
            metadata = output_dir / f"{stem}.json"
            if audio.exists() or metadata.exists():
                raise FileExistsError("A generation history filename collision occurred.")
        shutil.copy2(source_audio, audio)
        shutil.copy2(source_metadata, metadata)
        return GenerationResult(
            audio_path=audio,
            metadata_path=metadata,
            model_id=result.model_id,
            model_name=result.model_name,
            seed=result.seed,
            chunk_count=result.chunk_count,
        )

    def _saved_profile_id_for_path(self, voice_path: Path) -> str | None:
        candidate = voice_path.expanduser().resolve()
        for name in self.voice_library.list():
            resolved = self.voice_library.path_for(name)
            if resolved is None:
                continue
            try:
                if resolved.expanduser().resolve() != candidate:
                    continue
            except OSError:
                continue
            # Temporary internal bridge: VoiceLibrary owns alias/rename knowledge while
            # the legacy mirror exists. The record it returns is the canonical identity.
            record = self.voice_library._record_for_name(name)
            if record is not None:
                return record.profile.profile_id
        return None

    def _ephemeral_profile_for_path(self, voice_path: Path) -> tuple[str, object]:
        source = voice_path.expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError("The selected voice profile could not be found.")
        token = uuid.uuid4().hex
        artifact = self.artifact_store.register_file(
            source,
            artifact_id=f"ephemeral-reference-{token}",
            mime_type="audio/wav",
            copy=True,
        )
        profile_id = f"ephemeral-voice-{token}"
        try:
            self.profile_store.create(
                profile_id,
                "Temporary voice",
                source_kind=VoiceSourceKind.CLONE,
                reference=artifact,
                consistency_locked=False,
                metadata={"ephemeral": True},
            )
        except Exception:
            self.artifact_store.remove(artifact)
            raise
        return profile_id, artifact

    def _resolve_voice_identity(
        self,
        *,
        voice_profile_id: str | None,
        voice_path: str | Path | None,
    ) -> tuple[str, object | None]:
        if voice_profile_id:
            if self.profile_store.get(voice_profile_id) is None:
                raise FileNotFoundError(f"Voice profile '{voice_profile_id}' was not found.")
            return voice_profile_id, None
        if voice_path is None:
            raise ValueError("Choose a voice first, or add one here.")
        path = Path(voice_path)
        saved = self._saved_profile_id_for_path(path)
        if saved:
            return saved, None
        return self._ephemeral_profile_for_path(path)

    def generate(
        self,
        script: str,
        voice_path: str | Path | None = None,
        *,
        voice_profile_id: str | None = None,
        model_id: str,
        language_id: str = "en",
        exaggeration: float = 0.5,
        cfg_weight: float = 0.5,
        temperature: float = 0.8,
        repetition_penalty: float = 1.2,
        min_p: float = 0.05,
        top_p: float = 1.0,
        top_k: int = 1000,
        speech_speed: float = 1.0,
        raw_mode: bool = False,
        smart_chunking: bool = True,
        max_chars: int = 280,
        chunk_gap_seconds: float = 0.06,
        seed: int | None = None,
        progress_callback=None,
    ) -> GenerationResult:
        profile_id, ephemeral_artifact = self._resolve_voice_identity(
            voice_profile_id=voice_profile_id,
            voice_path=voice_path,
        )
        captured: list[GenerationResult] = []

        def preserve(result) -> None:
            captured.append(self._copy_private_result(result, self.output_dir))

        try:
            self.service.synthesize(
                SpeechSynthesisRequest(
                    text=script,
                    voice_profile_id=profile_id,
                    language=language_id,
                    engine_override=self._engine_id_for_model(model_id),
                ),
                execution=SynthesisExecutionSettings(
                    seed=seed,
                    device=self.device,
                    device_label=self.device_label,
                    exaggeration=float(exaggeration),
                    cfg_weight=float(cfg_weight),
                    temperature=float(temperature),
                    repetition_penalty=float(repetition_penalty),
                    min_p=float(min_p),
                    top_p=float(top_p),
                    top_k=int(top_k),
                    speech_speed=float(speech_speed),
                    raw_mode=bool(raw_mode),
                    smart_chunking=bool(smart_chunking),
                    max_chars=int(max_chars),
                    chunk_gap_seconds=float(chunk_gap_seconds),
                ),
                progress_callback=progress_callback,
                result_callback=preserve,
            )
        finally:
            if ephemeral_artifact is not None:
                self.profile_store.delete(profile_id)
                try:
                    self.artifact_store.remove(ephemeral_artifact)
                except (FileNotFoundError, ValueError):
                    pass
        if len(captured) != 1:
            raise RuntimeError("Speech Core did not return exactly one UI generation result.")
        return captured[0]

    def recent_outputs(self, limit: int = 20) -> list[str]:
        files = sorted(
            self.output_dir.glob("*.wav"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return [path.name for path in files[:limit]]

    def output_path(self, filename: str | None) -> Path | None:
        if not filename:
            return None
        safe = Path(filename).name
        path = self.output_dir / safe
        return path if path.exists() else None

    def metadata_path(self, filename: str | None) -> Path | None:
        path = self.output_path(filename)
        if path is None:
            return None
        metadata = path.with_suffix(".json")
        return metadata if metadata.exists() else None
