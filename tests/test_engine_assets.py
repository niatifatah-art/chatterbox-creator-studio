from __future__ import annotations

from studio.engine_assets import MODEL_ASSET_MANIFESTS, RUNTIME_MANIFESTS, RuntimeInstallMode
from studio.engine_registry import ENGINE_MANIFESTS, model_assets_for_engine, runtime_for_engine
from studio.protocol import EngineStatus


def test_every_supported_engine_has_a_declared_runtime():
    for engine in ENGINE_MANIFESTS.values():
        if engine.status == EngineStatus.SUPPORTED:
            assert engine.runtime_id in RUNTIME_MANIFESTS, engine.engine_id
            assert runtime_for_engine(engine.engine_id) is RUNTIME_MANIFESTS[engine.runtime_id]


def test_engine_model_assets_use_the_same_runtime():
    for engine in ENGINE_MANIFESTS.values():
        for asset in model_assets_for_engine(engine.engine_id):
            assert asset.model_id in MODEL_ASSET_MANIFESTS
            assert asset.runtime_id == engine.runtime_id


def test_current_chatterbox_routes_are_manifest_driven():
    expected = {
        "chatterbox-v3": "multilingual-v3",
        "chatterbox-turbo": "turbo",
        "chatterbox-nano": "nano",
    }
    for engine_id, model_id in expected.items():
        engine = ENGINE_MANIFESTS[engine_id]
        assert engine.status == EngineStatus.SUPPORTED
        assert engine.runtime_id == "chatterbox"
        assert engine.model_ids == (model_id,)
        asset = MODEL_ASSET_MANIFESTS[model_id]
        assert asset.runtime_id == "chatterbox"
        assert asset.repo_id.startswith("ResembleAI/")
        assert asset.weights_license == "MIT"


def test_kokoro_has_audited_isolated_runtime_and_model_but_is_not_auto_yet():
    engine = ENGINE_MANIFESTS["kokoro"]
    assert engine.status == EngineStatus.CATALOGUED
    assert engine.model_ids == ("kokoro-v1.0",)
    assert engine.languages == ("en",)
    runtime = runtime_for_engine("kokoro")
    assert runtime.install_mode == RuntimeInstallMode.ISOLATED
    assert runtime.requirements == ("kokoro==0.9.4",)
    asset = MODEL_ASSET_MANIFESTS["kokoro-v1.0"]
    assert asset.repo_id == "hexgrad/Kokoro-82M"
    assert asset.weights_license == "Apache-2.0"


def test_qwen_remains_catalogued_without_guessed_runtime_or_model_assets():
    engine = ENGINE_MANIFESTS["qwen3-tts"]
    assert engine.status == EngineStatus.CATALOGUED
    assert engine.model_ids == ()
    runtime = RUNTIME_MANIFESTS[engine.runtime_id]
    assert runtime.install_mode == RuntimeInstallMode.ISOLATED
    assert runtime.requirements == ()
