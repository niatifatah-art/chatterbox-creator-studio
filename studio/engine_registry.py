from __future__ import annotations

from dataclasses import dataclass

from studio.protocol import Capability


@dataclass(frozen=True, slots=True)
class EngineManifest:
    engine_id: str
    display_name: str
    family: str
    capabilities: frozenset[Capability]
    languages: tuple[str, ...]
    resource_tier: str
    code_license: str
    weights_license: str
    status: str = "supported"
    notes: str = ""

    def supports(self, *capabilities: Capability) -> bool:
        return all(capability in self.capabilities for capability in capabilities)

    @property
    def multilingual(self) -> bool:
        return "*" in self.languages or len(self.languages) > 1


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
        notes="CPU-friendly English cloning and expression tags.",
    ),
    "qwen3-tts": EngineManifest(
        engine_id="qwen3-tts",
        display_name="Smart Voice",
        family="qwen3-tts",
        capabilities=frozenset({
            Capability.SYNTHESIZE,
            Capability.VOICE_CLONE,
            Capability.VOICE_DESIGN,
            Capability.READY_VOICE,
        }),
        languages=("zh", "en", "ja", "ko", "de", "fr", "ru", "pt", "es", "it"),
        resource_tier="heavy",
        code_license="Apache-2.0",
        weights_license="Apache-2.0",
        status="catalogued",
        notes="Voice design, ready voices and cloning; runtime isolation required before shipping.",
    ),
    "kokoro": EngineManifest(
        engine_id="kokoro",
        display_name="Ultra Light",
        family="kokoro",
        capabilities=frozenset({Capability.SYNTHESIZE, Capability.READY_VOICE}),
        languages=("en", "ja", "zh", "es", "fr", "hi", "it", "pt"),
        resource_tier="ultra_light",
        code_license="Apache-2.0",
        weights_license="Apache-2.0",
        status="catalogued",
        notes="Tiny preset-voice engine; ideal candidate for low-resource machines.",
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
        status="catalogued",
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
        status="catalogued",
        notes="Alternative multilingual ASR candidate including Arabic; timing support must be certified before routing captions to it.",
    ),
}


def manifest_for(engine_id: str) -> EngineManifest:
    try:
        return ENGINE_MANIFESTS[engine_id]
    except KeyError as exc:
        raise ValueError(f"Unknown speech engine: {engine_id}") from exc


def engines_for(capability: Capability, *, include_catalogued: bool = False) -> tuple[EngineManifest, ...]:
    return tuple(
        manifest
        for manifest in ENGINE_MANIFESTS.values()
        if capability in manifest.capabilities and (include_catalogued or manifest.status == "supported")
    )
