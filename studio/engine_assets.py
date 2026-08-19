from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ModelProvider(str, Enum):
    HUGGINGFACE = "huggingface"


class RuntimeKind(str, Enum):
    PYTHON = "python"


class RuntimeInstallMode(str, Enum):
    """How a runtime is currently provisioned.

    `host_legacy` exists only while the shipped Gradio application still installs the
    Chatterbox dependency in its main environment. New conflicting engine families must
    use an isolated runtime rather than extending the host environment indefinitely.
    """

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
    source_revision: str | None = None
    code_license: str | None = None
    distribution_name: str | None = None
    notes: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelAssetManifest:
    """Downloadable model asset independent from the runtime that executes it."""

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
        display_name="Kokoro Runtime",
        install_mode=RuntimeInstallMode.ISOLATED,
        python_spec=">=3.10,<3.14",
        requirements=(f"kokoro=={KOKORO_PACKAGE_VERSION}",),
        source_revision=KOKORO_PACKAGE_VERSION,
        code_license="Apache-2.0",
        distribution_name="kokoro",
        notes=(
            "Official Kokoro 0.9.4 Python runtime. Phase 5 certifies English ready voices first; "
            "espeak-ng remains an optional English OOD fallback and is required before later espeak-based language routes are certified."
        ),
        metadata={
            "upstream": "https://github.com/hexgrad/kokoro",
            "english_ood_fallback": "espeak-ng",
        },
    ),
    "qwen3-tts": RuntimeManifest(
        runtime_id="qwen3-tts",
        display_name="Qwen3-TTS Runtime",
        install_mode=RuntimeInstallMode.ISOLATED,
        python_spec=">=3.10,<3.14",
        requirements=(),
        notes="Catalogued only. Exact runtime requirements are selected and audited in the Qwen phase.",
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
            "ve.pt",
            "t3_mtl23ls_v3.safetensors",
            "s3gen.pt",
            "grapheme_mtl_merged_expanded_v1.json",
            "conds.pt",
            "Cangjie5_TC.json",
        ),
        expected_files=(
            "ve.pt",
            "t3_mtl23ls_v3.safetensors",
            "s3gen.pt",
            "grapheme_mtl_merged_expanded_v1.json",
            "conds.pt",
            "Cangjie5_TC.json",
        ),
        weights_license="MIT",
        notes="Chatterbox Multilingual V3 managed model asset.",
    ),
    "turbo": ModelAssetManifest(
        model_id="turbo",
        runtime_id="chatterbox",
        provider=ModelProvider.HUGGINGFACE,
        repo_id="ResembleAI/chatterbox-turbo",
        revision_ref="main",
        allow_patterns=("*.safetensors", "*.json", "*.txt", "*.pt", "*.model", "*.yaml"),
        weights_license="MIT",
        estimated_size_gb=4.1,
        notes="Chatterbox Turbo managed model asset.",
    ),
    "nano": ModelAssetManifest(
        model_id="nano",
        runtime_id="chatterbox",
        provider=ModelProvider.HUGGINGFACE,
        repo_id="ResembleAI/chatterbox-nano",
        revision_ref="main",
        allow_patterns=("*.safetensors", "*.json", "*.txt", "*.pt", "*.model", "*.yaml"),
        weights_license="MIT",
        notes="Chatterbox Nano managed model asset.",
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
        notes=(
            "Official Kokoro v1.0 model plus ready-voice packs. Download selection resolves and stores the immutable Hub snapshot revision."
        ),
        metadata={
            "serialization": "torch-pth-weights-only",
            "ready_voice_directory": "voices",
            "sample_rate": "24000",
        },
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
    """Fail fast if a manifest points at an undefined runtime or invalid host identity."""

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
            raise ValueError(
                f"Host runtime '{runtime_id}' needs distribution_name for read-only readiness checks."
            )


validate_asset_registry()
