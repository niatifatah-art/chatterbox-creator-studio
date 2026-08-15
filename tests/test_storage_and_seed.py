import json
import random
import sys
import types

from studio.engine import ChatterboxEngine, _resolve_seed, _seed_everything
from studio.settings import DEFAULT_SETTINGS, SettingsStore
from studio.voices import VoiceLibrary


def test_settings_store_migrates_old_payload_with_new_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"voice": "narrator", "language": "ar", "cfg_weight": 0.42}), encoding="utf-8")

    loaded = SettingsStore(path).load()

    assert loaded["voice"] == "narrator"
    assert loaded["language"] == "ar"
    assert loaded["cfg_weight"] == 0.42
    assert loaded["model_id"] == DEFAULT_SETTINGS["model_id"]
    assert loaded["raw_mode"] is False
    assert loaded["smart_chunking"] is True
    assert loaded["seed"] == -1


def test_voice_library_sanitizes_names_and_stays_inside_directory(tmp_path):
    source = tmp_path / "source.wav"
    source.write_bytes(b"voice-data")
    library = VoiceLibrary(tmp_path / "voices")

    name, destination = library.save(str(source), "../../My Voice")

    assert name == "My-Voice"
    assert destination.parent == library.directory
    assert destination.read_bytes() == b"voice-data"
    assert library.list() == ["My-Voice"]
    assert library.path_for("../../My Voice") == destination


def test_output_history_rejects_path_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr("studio.engine.detect_device", lambda: ("cpu", "CPU"))
    engine = ChatterboxEngine(tmp_path / "outputs")
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"x")

    assert engine.output_path("../outside.wav") is None


def test_seed_helpers_are_stable(monkeypatch):
    seen = {"manual": [], "cuda": [], "cuda_all": [], "numpy": []}

    fake_torch = types.SimpleNamespace(
        manual_seed=lambda seed: seen["manual"].append(seed),
        cuda=types.SimpleNamespace(
            is_available=lambda: True,
            manual_seed=lambda seed: seen["cuda"].append(seed),
            manual_seed_all=lambda seed: seen["cuda_all"].append(seed),
        ),
    )
    fake_numpy = types.SimpleNamespace(
        random=types.SimpleNamespace(seed=lambda seed: seen["numpy"].append(seed))
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "numpy", fake_numpy)

    _seed_everything(12345)
    first = random.random()
    _seed_everything(12345)
    second = random.random()

    assert first == second
    assert seen["manual"] == [12345, 12345]
    assert seen["cuda"] == [12345, 12345]
    assert seen["cuda_all"] == [12345, 12345]
    assert seen["numpy"] == [12345, 12345]
    assert _resolve_seed(7) == 7
    assert 0 <= _resolve_seed(-1) < 2_147_483_647
