from __future__ import annotations

from dataclasses import dataclass

from studio.engine_assets import (
    MODEL_ASSET_MANIFESTS,
    RUNTIME_MANIFESTS,
    ModelAssetManifest,
    RuntimeManifest,
)
from studio.protocol import Capability, EngineStatus


@dataclass(frozen=True, slots=True)
class EngineManifest:
    """Small, model-free description of one routable speech implementation."""

    engine_id: str
    display_name: str
    family: str
    capabilities: frozenset[Capability]
    languages: tuple[str, ...]
    resource_tier: str
    code_license: str
    weights_license: str
    runtime_id: str
    model_ids: tuple[str, ...] = ()
    status: EngineStatus = EngineStatus.SUPPORTED
    notes: str = ""

    def supports(self, *capabilities: Capability) -> bool:
        return all(capability in self.capabilities for capability in capabilities)

    @property
    def multilingual(self) -> bool:
        return "*" in self.languages or len(self.languages) > 1

    @property
    def auto_routable(self) -> bool:
        return self.status == EngineStatus.SUPPORTED


QWEN_LANGUAGES = ("zh", "en", "ja", "ko", "de", "fr", "ru", "pt", "es", "it")


ENGINE_MANIFESTS: dict[str, EngineManifest] = {
    "chatterbox-v3": EngineManifest(
        engine_id="chatterbox-v3",
        display_name="Multilingual",
        family="chatterbox",
        capabilities=frozenset({Capability.SYNTHESIZE, Capability.VOICE_CLONE}),
        languages=(
            "ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it",
            "ja", "ko", "ms", "nl", "no", "pl", "pt", "ru", "sv", "sw", "tr", "zh",
        ),
        resource_tier="heavy",
        code_license="MIT",
        weights_license="MIT",
        runtime_id="chatterbox",
        model_ids=("multilingual-v3",),
        notes="Arabic and multilingual voice cloning.",
    ),
    "chatterbox-turbo": EngineManifest(
        engine_id="chatterbox-turbo",
        display_name="Expressive",
        family="chatterbox",
        capabilities=frozenset({Capability.SYNTHESIZE, Capability.VOICE_CLONE}),
        languages=("en",),
        resource_tier="medium",
        code_license="MIT",
        weights_license="MIT",
        runtime_id="chatterbox",
        model_ids=("turbo",),
        notes="Fast expressive English with native speech tags.",
    ),
    "chatterbox-nano": EngineManifest(
        engine_id="chatterbox-nano",
        display_name="Light Clone",
        family="chatterbox",
        capabilities=frozenset({Capability.SYNTHESIZE, Capability.VOICE_CLONE}),
        languages=("en",),
        resource_tier="light",
        code_license="MIT",
        weights_license="MIT",
        runtime_id="chatterbox",
        model_ids=("nano",),
        notes="CPU-friendly English cloning and expression tags.",
    ),
    "qwen3-clone": EngineManifest(
        engine_id="qwen3-clone",
        display_name="Smart Clone",
        family="qwen3-tts",
        capabilities=frozenset({Capability.SYNTHESIZE, Capability.VOICE_CLONE}),
        languages=QWEN_LANGUAGES,
        resource_tier="medium",
        code_license="Apache-2.0",
        weights_license="Apache-2.0",
        runtime_id="qwen3-tts",
        model_ids=("qwen3-0.6b-base",),
        status=EngineStatus.CATALOGUED,
        notes=(
            "Qwen3-TTS 0.6B Base multilingual cloning. Kept outside Auto until "
            "runtime, real-model quality and speaker-similarity gates pass."
        ),
    ),
    "qwen3-ready": EngineManifest(
        engine_id="qwen3-ready",
        display_name="Smart Ready",
        family="qwen3-tts",
        capabilities=frozenset({Capability.SYNTHESIZE, Capability.READY_VOICE}),
        languages=QWEN_LANGUAGES,
        resource_tier="medium",
        code_license="Apache-2.0",
        weights_license="Apache-2.0",
        runtime_id="qwen3-tts",
        model_ids=("qwen3-0.6b-custom",),
        status=EngineStatus.CATALOGUED,
        notes=(
            "Qwen3-TTS 0.6B CustomVoice with nine upstream preset speakers. "
            "Provider identity stays bound to the Voice Profile."
        ),
    ),
    "qwen3-voice-design": EngineManifest(
        engine_id="qwen3-voice-design",
        display_name="Voice Design",
        family="qwen3-tts",
        capabilities=frozenset({Capability.SYNTHESIZE, Capability.VOICE_DESIGN}),
        languages=QWEN_LANGUAGES,
        resource_tier="heavy",
        code_license="Apache-2.0",
        weights_license="Apache-2.0",
        runtime_id="qwen3-tts",
        model_ids=("qwen3-1.7b-voice-design",),
        status=EngineStatus.CATALOGUED,
        notes=(
            "Qwen3-TTS 1.7B free-form voice design. Direct design synthesis is "
            "allowed manually; durable character voices should later be frozen into a clone binding."
        ),
    ),
    "kokoro": EngineManifest(
        engine_id="kokoro",
        display_name="Kokoro Ready",
        family="kokoro",
        capabilities=frozenset({Capability.SYNTHESIZE, Capability.READY_VOICE}),
        languages=("en",),
        resource_tier="ultra_light",
        code_license="Apache-2.0",
        weights_license="Apache-2.0",
        runtime_id="kokoro",
        model_ids=("kokoro-v1.0",),
        status=EngineStatus.CATALOGUED,
        notes="82M ready-voice route. English execution is implemented; Auto remains disabled until certification is promoted.",
    ),
    "faster-whisper": EngineManifest(
        engine_id="faster-whisper",
        display_name="Local Transcription",
        family="whisper",
        capabilities=frozenset({Capability.TRANSCRIBE, Capability.VAD}),
        languages=("*",),
        resource_tier="medium",
        code_license="MIT",
        weights_license="model-dependent",
        runtime_id="faster-whisper",
        model_ids=(),
        notes="Current local STT backend with word timestamps and VAD support.",
    ),
    "whisper-cpp": EngineManifest(
        engine_id="whisper-cpp",
        display_name="Portable Transcription",
        family="whisper",
        capabilities=frozenset({Capability.TRANSCRIBE, Capability.VAD}),
        languages=("*",),
        resource_tier="light",
        code_license="MIT",
        weights_license="model-dependent",
        runtime_id="whisper-cpp",
        model_ids=(),
        status=EngineStatus.CATALOGUED,
        notes="Portable/quantized runtime candidate for low-resource and non-Python installs.",
    ),
    "fun-asr-mlt-nano": EngineManifest(
        engine_id="fun-asr-mlt-nano",
        display_name="Multilingual Transcription",
        family="fun-asr",
        capabilities=frozenset({Capability.TRANSCRIBE}),
        languages=("*",),
        resource_tier="medium",
        code_license="Apache-2.0",
        weights_license="Apache-2.0",
        runtime_id="fun-asr",
        model_ids=(),
        status=EngineStatus.CATALOGUED,
        notes="Alternative multilingual ASR candidate including Arabic; timing support must be certified before routing captions to it.",
    ),
}


def manifest_for(engine_id: str) -> EngineManifest:
    try:
        return ENGINE_MANIFESTS[engine_id]
    except KeyError as exc:
        raise ValueError(f"Unknown speech engine: {engine_id}") from exc


def runtime_for_engine(engine_id: str) -> RuntimeManifest:
    manifest = manifest_for(engine_id)
    try:
        return RUNTIME_MANIFESTS[manifest.runtime_id]
    except KeyError as exc:
        raise ValueError(
            f"Engine '{engine_id}' references unknown runtime '{manifest.runtime_id}'."
        ) from exc


def model_assets_for_engine(engine_id: str) -> tuple[ModelAssetManifest, ...]:
    manifest = manifest_for(engine_id)
    assets: list[ModelAssetManifest] = []
    for model_id in manifest.model_ids:
        try:
            asset = MODEL_ASSET_MANIFESTS[model_id]
        except KeyError as exc:
            raise ValueError(
                f"Engine '{engine_id}' references unknown model asset '{model_id}'."
            ) from exc
        if asset.runtime_id != manifest.runtime_id:
            raise ValueError(
                f"Engine '{engine_id}' and model '{model_id}' disagree on runtime identity."
            )
        assets.append(asset)
    return tuple(assets)


def engines_for(capability: Capability, *, include_catalogued: bool = False) -> tuple[EngineManifest, ...]:
    return tuple(
        manifest
        for manifest in ENGINE_MANIFESTS.values()
        if capability in manifest.capabilities and (include_catalogued or manifest.auto_routable)
    )


def validate_engine_registry() -> None:
    """Validate executable/configured routes while allowing research-only catalogue rows."""

    for engine_id, manifest in ENGINE_MANIFESTS.items():
        if manifest.engine_id != engine_id:
            raise ValueError(
                f"Engine manifest key '{engine_id}' disagrees with engine_id '{manifest.engine_id}'."
            )
        must_resolve_runtime = bool(manifest.model_ids) or manifest.status == EngineStatus.SUPPORTED
        if must_resolve_runtime:
            runtime_for_engine(engine_id)
        for asset in model_assets_for_engine(engine_id):
            if asset.runtime_id != manifest.runtime_id:
                raise ValueError(
                    f"Engine '{engine_id}' has an incompatible model asset runtime."
                )
        if manifest.status == EngineStatus.SUPPORTED and not manifest.runtime_id:
            raise ValueError(f"Supported engine '{engine_id}' has no runtime identity.")


validate_engine_registry()
