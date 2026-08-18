from __future__ import annotations

import json
import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

from studio.core_engine import CoreGenerationEngine
from studio.engine import ChatterboxEngine
from studio.model_manager import LocalModelStatus
from studio.voices import VoiceLibrary


def _write_wav(path: Path, seconds: float = 0.15, sample_rate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        for index in range(int(seconds * sample_rate)):
            sample = int(3000 * math.sin(2 * math.pi * 180 * index / sample_rate))
            handle.writeframesraw(struct.pack("<h", sample))


class FakeModelManager:
    def __init__(self, root: Path):
        self.root = root
        self.selected: dict[str, tuple[Path, str]] = {}

    def select_snapshot(self, model_id: str, path: str | Path, *, revision: str | None = None) -> LocalModelStatus:
        snapshot = Path(path).resolve()
        self.selected[model_id] = (snapshot, str(revision or snapshot.name))
        return self.status(model_id)

    def status(self, model_id: str) -> LocalModelStatus:
        if model_id != "nano":
            return LocalModelStatus(model_id, False, None, None, 0.0)
        selected = self.selected.get(model_id)
        if selected is not None:
            snapshot, revision = selected
            return LocalModelStatus(model_id, snapshot.is_dir(), str(snapshot), revision, 0.1)
        snapshot = self.root / "nano"
        snapshot.mkdir(parents=True, exist_ok=True)
        return LocalModelStatus(model_id, True, str(snapshot), "nano-revision", 0.1)


@dataclass
class FakeResult:
    audio_path: Path
    metadata_path: Path
    model_id: str
    model_name: str
    seed: int
    chunk_count: int


class FakeNative:
    instances: list["FakeNative"] = []

    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.device = "cpu"
        self.device_label = "CPU"
        self.loaded = False
        self.loaded_model_id = None
        self.model_paths: dict[str, str] = {}
        self.calls: list[dict] = []
        FakeNative.instances.append(self)

    def set_device(self, device: str, label: str | None = None) -> None:
        self.device = device
        self.device_label = label or device.upper()

    def set_model_path(self, model_id: str, path: str | Path | None) -> None:
        self.model_paths[model_id] = str(path) if path is not None else ""

    def load_model(self, model_id: str, progress_callback=None) -> None:
        self.loaded = True
        self.loaded_model_id = model_id
        if progress_callback:
            progress_callback("Model ready", 1, 1)

    def unload(self) -> None:
        self.loaded = False
        self.loaded_model_id = None

    def generate(self, **kwargs):
        self.calls.append(dict(kwargs))
        output = self.output_dir / "nano_fake.wav"
        _write_wav(output, seconds=0.3)
        metadata = output.with_suffix(".json")
        metadata.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "model": {"id": kwargs["model_id"]},
                    "voice_file": Path(kwargs["voice_path"]).name,
                    "original_script": kwargs["script"],
                    "seed": kwargs.get("seed", 123),
                    "mode": "studio",
                    "smart_chunking": bool(kwargs.get("smart_chunking", True)),
                    "chunk_count": 1,
                }
            ),
            encoding="utf-8",
        )
        return FakeResult(output, metadata, kwargs["model_id"], "Nano", int(kwargs.get("seed") or 123), 1)


def _core_backed_engine(tmp_path: Path, monkeypatch):
    FakeNative.instances.clear()
    monkeypatch.setattr("studio.core_engine.NativeChatterboxEngine", FakeNative)
    outputs = tmp_path / "outputs"
    data = tmp_path / "data"
    library = VoiceLibrary(data / "voices", core_directory=data / "speech-core")
    source = tmp_path / "voice.wav"
    _write_wav(source)
    name, saved_path = library.save(source, "Creator")
    manager = FakeModelManager(tmp_path / "models")
    engine = CoreGenerationEngine(
        outputs,
        data / "speech-core",
        model_manager=manager,
        profile_store=library.profile_store,
        artifact_store=library.artifact_store,
    )
    return engine, library, name, saved_path


def test_public_chatterbox_constructor_is_now_the_core_compatibility_facade(tmp_path, monkeypatch):
    monkeypatch.setattr("studio.engine.detect_device", lambda: ("cpu", "CPU"))
    engine = ChatterboxEngine(tmp_path / "outputs")
    assert isinstance(engine, CoreGenerationEngine)


def test_legacy_set_model_path_synchronizes_native_and_core_model_state(tmp_path, monkeypatch):
    engine, _library, _name, _saved_path = _core_backed_engine(tmp_path, monkeypatch)
    snapshot = tmp_path / "managed" / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)

    engine.set_model_path("nano", snapshot)

    assert Path(FakeNative.instances[0].model_paths["nano"]).resolve() == snapshot.resolve()
    status = engine.model_manager.status("nano")
    assert Path(status.snapshot_path or "").resolve() == snapshot.resolve()
    assert status.revision == "abc123"


def test_saved_legacy_voice_path_generates_through_core_and_preserves_history_files(tmp_path, monkeypatch):
    engine, library, name, saved_path = _core_backed_engine(tmp_path, monkeypatch)

    result = engine.generate(
        script="Hello through the UI compatibility facade.",
        voice_path=saved_path,
        model_id="nano",
        language_id="en",
        seed=77,
        smart_chunking=False,
    )

    assert result.audio_path.parent == engine.output_dir
    assert result.audio_path.is_file()
    assert result.metadata_path.is_file()
    assert engine.output_path(result.audio_path.name) == result.audio_path
    assert result.audio_path.name in engine.recent_outputs()
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["original_script"].startswith("Hello through")
    assert FakeNative.instances and FakeNative.instances[0].calls[0]["model_id"] == "nano"
    assert Path(FakeNative.instances[0].calls[0]["voice_path"]).resolve() != saved_path.resolve()
    assert library.list() == [name]


def test_arbitrary_cli_reference_uses_ephemeral_profile_and_cleans_it_after_generation(tmp_path, monkeypatch):
    FakeNative.instances.clear()
    monkeypatch.setattr("studio.core_engine.NativeChatterboxEngine", FakeNative)
    outputs = tmp_path / "outputs"
    data = tmp_path / "data"
    engine = CoreGenerationEngine(outputs, data / "speech-core", model_manager=FakeModelManager(tmp_path / "models"))
    raw = tmp_path / "outside-reference.wav"
    _write_wav(raw)

    before_profiles = set(engine.profile_store.list_ids())
    result = engine.generate(
        script="CLI raw reference compatibility.",
        voice_path=raw,
        model_id="nano",
        language_id="en",
        seed=12,
    )
    after_profiles = set(engine.profile_store.list_ids())

    assert result.audio_path.is_file()
    assert before_profiles == after_profiles
    assert not any(path.name.startswith("ephemeral-reference-") for path in engine.artifact_store.directory.glob("*"))


def test_process_owned_native_engine_is_reused_between_reliable_takes(tmp_path, monkeypatch):
    engine, _library, _name, saved_path = _core_backed_engine(tmp_path, monkeypatch)

    first = engine.generate(script="Take one.", voice_path=saved_path, model_id="nano", language_id="en", seed=1)
    second = engine.generate(script="Take two.", voice_path=saved_path, model_id="nano", language_id="en", seed=2)

    assert first.audio_path.is_file() and second.audio_path.is_file()
    assert len(FakeNative.instances) == 1
    assert len(FakeNative.instances[0].calls) == 2
