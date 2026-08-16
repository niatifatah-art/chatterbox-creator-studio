from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ModelCapabilities:
    multilingual: bool
    expressive_tags: bool
    exaggeration: bool
    cfg_weight: bool
    min_p: bool
    top_p: bool = True
    top_k: bool = False


@dataclass(frozen=True)
class ModelProfile:
    exaggeration: float
    cfg_weight: float
    temperature: float
    repetition_penalty: float
    min_p: float
    top_p: float
    top_k: int
    speech_speed: float = 1.0


# Multilingual values stay close to upstream Chatterbox guidance. In particular,
# Creator intentionally preserves the recipe used by the project before v1.1.1 so
# a sound a creator already likes does not silently change after this UX update.
# Turbo/Nano use their own sampling family; controls that their current upstream API
# ignores are kept as harmless placeholders for the shared generation protocol and
# are hidden from the product UI.
MODEL_PROFILES: dict[str, dict[str, ModelProfile]] = {
    "multilingual-v3": {
        "Natural": ModelProfile(0.50, 0.50, 0.80, 1.20, 0.05, 1.00, 1000, 1.00),
        "Creator": ModelProfile(0.65, 0.30, 0.80, 1.20, 0.05, 1.00, 1000, 1.00),
        "Stable": ModelProfile(0.45, 0.45, 0.65, 1.25, 0.05, 0.95, 1000, 1.00),
        "Expressive": ModelProfile(0.75, 0.30, 0.85, 1.20, 0.05, 1.00, 1000, 1.00),
    },
    "turbo": {
        "Natural": ModelProfile(0.50, 0.50, 0.80, 1.20, 0.00, 0.95, 1000, 1.00),
        "Creator": ModelProfile(0.50, 0.50, 0.78, 1.20, 0.00, 0.95, 1000, 1.00),
        "Stable": ModelProfile(0.50, 0.50, 0.65, 1.25, 0.00, 0.90, 750, 1.00),
        "Expressive": ModelProfile(0.50, 0.50, 0.95, 1.15, 0.00, 0.98, 1000, 1.00),
    },
    "nano": {
        "Natural": ModelProfile(0.50, 0.50, 0.80, 1.20, 0.00, 0.95, 1000, 1.00),
        "Creator": ModelProfile(0.50, 0.50, 0.75, 1.20, 0.00, 0.92, 800, 1.00),
        "Stable": ModelProfile(0.50, 0.50, 0.65, 1.25, 0.00, 0.90, 600, 1.00),
        "Expressive": ModelProfile(0.50, 0.50, 0.90, 1.15, 0.00, 0.97, 900, 1.00),
    },
}


MODEL_CAPABILITIES: dict[str, ModelCapabilities] = {
    "multilingual-v3": ModelCapabilities(
        multilingual=True,
        expressive_tags=False,
        exaggeration=True,
        cfg_weight=True,
        min_p=True,
        top_p=True,
        top_k=False,
    ),
    "turbo": ModelCapabilities(
        multilingual=False,
        expressive_tags=True,
        exaggeration=False,
        cfg_weight=False,
        min_p=False,
        top_p=True,
        top_k=True,
    ),
    "nano": ModelCapabilities(
        multilingual=False,
        expressive_tags=True,
        exaggeration=False,
        cfg_weight=False,
        min_p=False,
        top_p=True,
        top_k=True,
    ),
}


STYLE_NAMES = tuple(MODEL_PROFILES["multilingual-v3"])


def profile_for(model_id: str, style: str) -> ModelProfile:
    family = MODEL_PROFILES.get(model_id, MODEL_PROFILES["multilingual-v3"])
    return family.get(style, family["Natural"])


def profile_values(model_id: str, style: str) -> tuple[float, float, float, float, float, float, int, float]:
    profile = profile_for(model_id, style)
    return (
        profile.exaggeration,
        profile.cfg_weight,
        profile.temperature,
        profile.repetition_penalty,
        profile.min_p,
        profile.top_p,
        profile.top_k,
        profile.speech_speed,
    )


def profile_dict(model_id: str, style: str) -> dict[str, float | int]:
    return asdict(profile_for(model_id, style))


def capabilities_for(model_id: str) -> ModelCapabilities:
    return MODEL_CAPABILITIES.get(model_id, MODEL_CAPABILITIES["multilingual-v3"])


def language_control_needed(model_id: str | None) -> bool:
    # Auto can resolve to multilingual depending on the text, so the language hint
    # remains available. Explicit English-only models do not need a language control.
    return model_id in {None, "multilingual-v3"}
