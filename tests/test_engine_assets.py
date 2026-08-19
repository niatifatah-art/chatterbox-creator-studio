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


def test_kokoro_has_audited_deterministic_english_runtime_and_model_but_is_not_auto_yet():
    engine = ENGINE_MANIFESTS["kokoro"]
    assert engine.status == EngineStatus.CATALOGUED
    assert engine.model_ids == ("kokoro-v1.0",)
    assert engine.languages == ("en",)
    runtime = runtime_for_engine("kokoro")
    assert runtime.install_mode == RuntimeInstallMode.ISOLATED
    assert runtime.no_deps_requirements == ("kokoro==0.9.4",)
    assert any(requirement.startswith("torch==") for requirement in runtime.bootstrap_requirements)
    assert runtime.bootstrap_index_url and "pytorch.org/whl/cpu" in runtime.bootstrap_index_url
    assert "attrs==26.1.0" in runtime.requirements
    assert any(requirement.startswith("misaki==0.9.4") for requirement in runtime.requirements)
    assert any("en_core_web_sm-3.8.0" in requirement for requirement in runtime.requirements)
    assert not any("espeak" in requirement.lower() or "phonemizer" in requirement.lower() for requirement in runtime.requirements)
    assert runtime.metadata["english_ood_fallback"] == "disabled"
    assert runtime.metadata["undeclared_dependency_pin"] == "attrs==26.1.0"
    asset = MODEL_ASSET_MANIFESTS["kokoro-v1.0"]
    assert asset.repo_id == "hexgrad/Kokoro-82M"
    assert asset.weights_license == "Apache-2.0"


def test_qwen_routes_have_one_reason_each_and_share_one_audited_runtime():
    expected = {
        "qwen3-clone": ("qwen3-0.6b-base", "voice_clone"),
        "qwen3-ready": ("qwen3-0.6b-custom", "ready_voice"),
        "qwen3-voice-design": ("qwen3-1.7b-voice-design", "voice_design"),
    }
    runtime = RUNTIME_MANIFESTS["qwen3-tts"]
    assert runtime.install_mode == RuntimeInstallMode.ISOLATED
    assert runtime.no_deps_requirements == ("qwen-tts==0.1.1",)
    assert "transformers==4.57.3" in runtime.requirements
    assert "accelerate==1.12.0" in runtime.requirements
    assert any(requirement == "torch==2.13.0" for requirement in runtime.bootstrap_requirements)
    assert any(requirement == "torchaudio==2.11.0" for requirement in runtime.bootstrap_requirements)
    assert runtime.bootstrap_index_url and "pytorch.org/whl/cpu" in runtime.bootstrap_index_url
    assert not any(requirement.startswith("gradio") for requirement in runtime.requirements)

    for engine_id, (model_id, role) in expected.items():
        engine = ENGINE_MANIFESTS[engine_id]
        assert engine.status == EngineStatus.CATALOGUED
        assert engine.runtime_id == "qwen3-tts"
        assert engine.model_ids == (model_id,)
        asset = MODEL_ASSET_MANIFESTS[model_id]
        assert asset.repo_id.startswith("Qwen/Qwen3-TTS-12Hz-")
        assert asset.weights_license == "Apache-2.0"
        assert asset.metadata["role"] == role
        assert "speech_tokenizer/model.safetensors" in asset.expected_files


def test_qwen_does_not_add_redundant_1_7b_clone_or_custom_models_before_benchmarking():
    repo_ids = {asset.repo_id for asset in MODEL_ASSET_MANIFESTS.values()}
    assert "Qwen/Qwen3-TTS-12Hz-1.7B-Base" not in repo_ids
    assert "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice" not in repo_ids
