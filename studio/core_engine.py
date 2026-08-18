from __future__ import annotations

import shutil
from pathlib import Path

from studio.engine import ChatterboxEngine, GenerationResult
from studio.engine_registry import ENGINE_MANIFESTS
from studio.model_manager import LocalModelManager
from studio.protocol import Capability, SpeechSynthesisRequest
from studio.synthesis import SpeechSynthesisService, SynthesisExecutionSettings
from studio.voice_profile_store import VoiceProfileStore
from studio.artifact_store import ArtifactStore


class CoreGenerationEngine:
    """Temporary controller facade that keeps the existing UI/reliability contract.

    Generation itself is owned by `SpeechSynthesisService`. This facade only adapts the
    legacy controller's `engine.generate(...) -> GenerationResult` expectation and
    preserves the current output/history files while the Gradio shell is cut over.

    New engine families must never be added here. Phase 4 introduces generic engine
    runtime dispatch behind Speech Core; this facade disappears with the legacy shell.
    """

    def __init__(
        self,
        output_dir: str | Path,
        core_dir: str | Path,
        *,
        model_manager: LocalModelManager,
        profile_store: VoiceProfileStore,
        artifact_store: ArtifactStore,
    ):
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.core_dir = Path(core_dir).expanduser().resolve()
        self.core_dir.mkdir(parents=True, exist_ok=True)
        self.model_manager = model_manager
        self.profile_store = profile_store
        self.artifact_store = artifact_store

        # One process-owned engine preserves the current in-memory model behavior for
        # Best-of, retries, Batch and Compare. Speech Core still owns route/reference /
        # revision checks and the actual generate call.
        self._engine = ChatterboxEngine(self.core_dir / "generation-work")

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
        # Keep the shared runtime ready for the exact selected snapshot. Speech Core
        # independently verifies the same model-manager state before every generation.
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
        # Timestamp-based engine names are already collision-resistant. Never overwrite
        # an existing history entry if a filesystem clock collision occurs.
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

    def generate(
        self,
        script: str,
        *,
        voice_profile_id: str,
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
        if not voice_profile_id:
            raise ValueError("Choose a voice first, or add one here.")
        captured: list[GenerationResult] = []

        def preserve(result) -> None:
            captured.append(self._copy_private_result(result, self.output_dir))

        self.service.synthesize(
            SpeechSynthesisRequest(
                text=script,
                voice_profile_id=voice_profile_id,
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
