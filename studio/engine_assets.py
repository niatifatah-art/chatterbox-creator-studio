from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ModelProvider(str, Enum):
    HUGGINGFACE = "huggingface"


class RuntimeKind(str, Enum):
    PYTHON = "python"


class RuntimeInstallMode(str, Enum):
    HOST_LEGACY = "host_legacy"
    ISOLATED = "isolated"


@dataclass(frozen=True, slots=True)
class RuntimeManifest:
    runtime_id: str
    display_name: str
    kind: RuntimeKind = RuntimeKind.PYTHON
    install_mode: RuntimeInstallMode = RuntimeInstallMode.ISOLATED
    python_spec: str = ">=3.10,<3.14"
    requirements: tuple[str, ...] = ()
    no_deps_requirements: tuple[str, ...] = ()
    bootstrap_requirements: tuple[str, ...] = ()
    bootstrap_index_url: str | None = None
    source_revision: str | None = None
    code_license: str | None = None
    distribution_name: str | None = None
    notes: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelAssetManifest:
    model_id: str
    runtime_id: str
    provider: ModelProvider
    repo_id: str
    revision_ref: str = "main"
    allow_patterns: tuple[str, ...] = ()
    expected_files: tuple[str, ...] = ()
    weights_license: str | None = None
    estimated_size_gb: float | None = None
    notes: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


CHATTERBOX_UPSTREAM_REVISION = "5de7a54aa4e5e2baadb0182dde554908b48b85c2"
KOKORO_PACKAGE_VERSION = "0.9.4"
KOKORO_CPU_TORCH_VERSION = "2.13.0"
QWEN_TTS_PACKAGE_VERSION = "0.1.1"
QWEN_CPU_TORCH_VERSION = "2.13.0"
QWEN_CPU_TORCHAUDIO_VERSION = "2.11.0"
PYTORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
SPACY_EN_SM_WHEEL = (
    "en-core-web-sm @ "
    "https://github.com/explosion/spacy-models/releases/download/"
    "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
)

QWEN_MODEL_PATTERNS = (
    "config.json",
    "generation_config.json",
    "merges.txt",
    "model.safetensors",
    "preprocessor_config.json",
    "tokenizer_config.json",
    "vocab.json",
    "speech_tokenizer/*",
    "README.md",
)
QWEN_EXPECTED_FILES = (
    "config.json",
    "model.safetensors",
    "speech_tokenizer/config.json",
    "speech_tokenizer/model.safetensors",
)


RUNTIME_MANIFESTS: dict[str, RuntimeManifest] = {
    "chatterbox": RuntimeManifest(
        runtime_id="chatterbox",
        display_name="Chatterbox Runtime",
        install_mode=RuntimeInstallMode.HOST_LEGACY,
        python_spec=">=3.10,<3.14",
        requirements=(
            f"chatterbox-tts @ git+https://github.com/resemble-ai/chatterbox.git@{CHATTERBOX_UPSTREAM_REVISION}",
        ),
        source_revision=CHATTERBOX_UPSTREAM_REVISION,
        code_license="MIT",
        distribution_name="chatterbox-tts",
        notes="Current shipped runtime. Kept host-compatible while new engine families use isolated runtimes.",
    ),
    "kokoro": RuntimeManifest(
        runtime_id="kokoro",
        display_name="Kokoro CPU Runtime",
        install_mode=RuntimeInstallMode.ISOLATED,
        python_spec=">=3.10,<3.14",
        requirements=(
            "attrs==26.1.0",
            "huggingface-hub==1.28.0",
            "loguru==0.7.3",
            "misaki==0.9.4",
            "num2words==0.5.14",
            "numpy==2.4.2",
            "spacy==3.8.11",
            "transformers==5.15.1",
            SPACY_EN_SM_WHEEL,
        ),
        no_deps_requirements=(f"kokoro=={KOKORO_PACKAGE_VERSION}",),
        bootstrap_requirements=(f"torch=={KOKORO_CPU_TORCH_VERSION}",),
        bootstrap_index_url=PYTORCH_CPU_INDEX,
        source_revision=KOKORO_PACKAGE_VERSION,
        code_license="Apache-2.0",
        distribution_name="kokoro",
        notes=(
            "Audited English ready-voice runtime: CPU-only PyTorch, Kokoro 0.9.4, Misaki 0.9.4, "
            "attrs for Kokoro's undeclared custom-STFT import, and the official spaCy English small pipeline. "
            "The optional eSpeak/phonemizer fallback is intentionally omitted so this lightweight route remains "
            "deterministic and does not inherit an external native-data dependency. OOV fallback is disabled; "
            "pronunciation hints/QA are the future product-level recovery path."
        ),
        metadata={
            "upstream": "https://github.com/hexgrad/kokoro",
            "compute_tier": "cpu",
            "english_ood_fallback": "disabled",
            "spacy_pipeline": "en_core_web_sm-3.8.0",
            "undeclared_dependency_pin": "attrs==26.1.0",
        },
    ),
    "qwen3-tts": RuntimeManifest(
        runtime_id="qwen3-tts",
        display_name="Qwen3-TTS CPU Baseline Runtime",
        install_mode=RuntimeInstallMode.ISOLATED,
        python_spec=">=3.10,<3.14",
        # Upstream includes Gradio for its demo. Voice Studio never imports the demo,
        # so install qwen-tts without dependencies and provide the inference surface only.
        requirements=(
            "accelerate==1.12.0",
            "transformers==4.57.3",
            "librosa>=0.11,<1",
            "soundfile>=0.13,<1",
            "sox>=1.5,<2",
            "onnxruntime>=1.23,<2",
            "einops>=0.8,<1",
        ),
        no_deps_requirements=(f"qwen-tts=={QWEN_TTS_PACKAGE_VERSION}",),
        # CPU is the deterministic portable baseline. CUDA wheels become an explicit
        # runtime flavour later instead of an accidental multi-GB dependency.
        bootstrap_requirements=(
            f"torch=={QWEN_CPU_TORCH_VERSION}",
            f"torchaudio=={QWEN_CPU_TORCHAUDIO_VERSION}",
        ),
        bootstrap_index_url=PYTORCH_CPU_INDEX,
        source_revision=QWEN_TTS_PACKAGE_VERSION,
        code_license="Apache-2.0",
        distribution_name="qwen-tts",
        notes=(
            "Audited isolated Qwen3-TTS inference surface using qwen-tts 0.1.1. "
            "The baseline runtime intentionally uses CPU PyTorch so installation is portable "
            "and does not silently pull multi-gigabyte CUDA wheels. GPU runtime flavours remain "
            "a separate product capability, not an implicit dependency."
        ),
        metadata={
            "upstream": "https://github.com/QwenLM/Qwen3-TTS",
            "compute_tier": "cpu_baseline",
            "package_version": QWEN_TTS_PACKAGE_VERSION,
            "demo_dependency": "gradio omitted",
        },
    ),
    "faster-whisper": RuntimeManifest(
        runtime_id="faster-whisper",
        display_name="Faster Whisper Runtime",
        install_mode=RuntimeInstallMode.ISOLATED,
        python_spec=">=3.10,<3.14",
        requirements=(),
        notes="Current optional helper remains legacy-installed until the STT phase moves it behind Speech Core.",
    ),
    "whisper-cpp": RuntimeManifest(
        runtime_id="whisper-cpp",
        display_name="whisper.cpp Runtime",
        kind=RuntimeKind.PYTHON,
        install_mode=RuntimeInstallMode.ISOLATED,
        requirements=(),
        notes="Catalogued portable STT candidate; installer is intentionally undefined until benchmarked.",
    ),
}


MODEL_ASSET_MANIFESTS: dict[str, ModelAssetManifest] = {
    "multilingual-v3": ModelAssetManifest(
        model_id="multilingual-v3",
        runtime_id="chatterbox",
        provider=ModelProvider.HUGGINGFACE,
        repo_id="ResembleAI/chatterbox",
        revision_ref="main",
        allow_patterns=(
            "ve.pt", "t3_mtl23ls_v3.safetensors", "s3gen.pt",
            "grapheme_mtl_merged_expanded_v1.json", "conds.pt", "Cangjie5_TC.json",
        ),
        expected_files=(
            "ve.pt", "t3_mtl23ls_v3.safetensors", "s3gen.pt",
            "grapheme_mtl_merged_expanded_v1.json", "conds.pt", "Cangjie5_TC.json",
        ),
        weights_license="MIT",
        notes="Chatterbox Multilingual V3 managed model asset.",
    ),
    "turbo": ModelAssetManifest(
        model_id="turbo", runtime_id="chatterbox", provider=ModelProvider.HUGGINGFACE,
        repo_id="ResembleAI/chatterbox-turbo", revision_ref="main",
        allow_patterns=("*.safetensors", "*.json", "*.txt", "*.pt", "*.model", "*.yaml"),
        weights_license="MIT", estimated_size_gb=4.1, notes="Chatterbox Turbo managed model asset.",
    ),
    "nano": ModelAssetManifest(
        model_id="nano", runtime_id="chatterbox", provider=ModelProvider.HUGGINGFACE,
        repo_id="ResembleAI/chatterbox-nano", revision_ref="main",
        allow_patterns=("*.safetensors", "*.json", "*.txt", "*.pt", "*.model", "*.yaml"),
        weights_license="MIT", notes="Chatterbox Nano managed model asset.",
    ),
    "kokoro-v1.0": ModelAssetManifest(
        model_id="kokoro-v1.0",
        runtime_id="kokoro",
        provider=ModelProvider.HUGGINGFACE,
        repo_id="hexgrad/Kokoro-82M",
        revision_ref="main",
        allow_patterns=("config.json", "kokoro-v1_0.pth", "voices/*.pt", "VOICES.md", "README.md"),
        expected_files=("config.json", "kokoro-v1_0.pth"),
        weights_license="Apache-2.0",
        estimated_size_gb=0.36,
        notes="Official Kokoro v1.0 model plus ready-voice packs; selected by immutable resolved Hub revision.",
        metadata={"serialization": "torch-pth-weights-only", "ready_voice_directory": "voices", "sample_rate": "24000"},
    ),
    "qwen3-0.6b-base": ModelAssetManifest(
        model_id="qwen3-0.6b-base",
        runtime_id="qwen3-tts",
        provider=ModelProvider.HUGGINGFACE,
        repo_id="Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        revision_ref="main",
        allow_patterns=QWEN_MODEL_PATTERNS,
        expected_files=QWEN_EXPECTED_FILES,
        weights_license="Apache-2.0",
        estimated_size_gb=2.52,
        notes="Qwen3-TTS 0.6B Base: multilingual rapid voice cloning.",
        metadata={"role": "voice_clone", "parameter_class": "0.6B", "sample_rate": "24000"},
    ),
    "qwen3-0.6b-custom": ModelAssetManifest(
        model_id="qwen3-0.6b-custom",
        runtime_id="qwen3-tts",
        provider=ModelProvider.HUGGINGFACE,
        repo_id="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        revision_ref="main",
        allow_patterns=QWEN_MODEL_PATTERNS,
        expected_files=QWEN_EXPECTED_FILES,
        weights_license="Apache-2.0",
        estimated_size_gb=2.50,
        notes="Qwen3-TTS 0.6B CustomVoice: nine upstream ready voices across the supported languages.",
        metadata={"role": "ready_voice", "parameter_class": "0.6B", "sample_rate": "24000"},
    ),
    "qwen3-1.7b-voice-design": ModelAssetManifest(
        model_id="qwen3-1.7b-voice-design",
        runtime_id="qwen3-tts",
        provider=ModelProvider.HUGGINGFACE,
        repo_id="Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        revision_ref="main",
        allow_patterns=QWEN_MODEL_PATTERNS,
        expected_files=QWEN_EXPECTED_FILES,
        weights_license="Apache-2.0",
        estimated_size_gb=4.52,
        notes="Qwen3-TTS 1.7B VoiceDesign: free-form voice creation from natural-language descriptions.",
        metadata={"role": "voice_design", "parameter_class": "1.7B", "sample_rate": "24000"},
    ),
}


def runtime_manifest(runtime_id: str) -> RuntimeManifest:
    try:
        return RUNTIME_MANIFESTS[runtime_id]
    except KeyError as exc:
        raise ValueError(f"Unknown runtime '{runtime_id}'.") from exc


def model_asset_manifest(model_id: str) -> ModelAssetManifest:
    try:
        return MODEL_ASSET_MANIFESTS[model_id]
    except KeyError as exc:
        raise ValueError(f"Unknown model '{model_id}'.") from exc


def validate_asset_registry() -> None:
    for model_id, model in MODEL_ASSET_MANIFESTS.items():
        if model.model_id != model_id:
            raise ValueError(f"Model manifest key '{model_id}' disagrees with model_id '{model.model_id}'.")
        if model.runtime_id not in RUNTIME_MANIFESTS:
            raise ValueError(f"Model '{model_id}' references unknown runtime '{model.runtime_id}'.")
        if model.provider == ModelProvider.HUGGINGFACE and "/" not in model.repo_id:
            raise ValueError(f"Hugging Face model '{model_id}' has an invalid repo_id.")
    for runtime_id, runtime in RUNTIME_MANIFESTS.items():
        if runtime.runtime_id != runtime_id:
            raise ValueError(f"Runtime manifest key '{runtime_id}' disagrees with runtime_id '{runtime.runtime_id}'.")
        if runtime.install_mode == RuntimeInstallMode.HOST_LEGACY and runtime.requirements and not runtime.distribution_name:
            raise ValueError(f"Host runtime '{runtime_id}' needs distribution_name for read-only readiness checks.")
        if runtime.bootstrap_index_url and not runtime.bootstrap_requirements:
            raise ValueError(f"Runtime '{runtime_id}' declares a bootstrap index without bootstrap packages.")


validate_asset_registry()
