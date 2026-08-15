from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class GenerationPreset:
    exaggeration: float
    cfg_weight: float
    temperature: float
    repetition_penalty: float
    min_p: float
    top_p: float
    speech_speed: float = 1.0


PRESETS: dict[str, GenerationPreset] = {
    "Natural": GenerationPreset(0.50, 0.50, 0.80, 1.20, 0.05, 1.00, 1.00),
    "Creator": GenerationPreset(0.65, 0.30, 0.80, 1.20, 0.05, 1.00, 0.94),
    "Stable": GenerationPreset(0.45, 0.45, 0.65, 1.25, 0.05, 0.95, 0.98),
    "Expressive": GenerationPreset(0.75, 0.30, 0.85, 1.20, 0.05, 1.00, 0.96),
}


def preset_values(name: str) -> tuple[float, float, float, float, float, float, float]:
    preset = PRESETS.get(name, PRESETS["Natural"])
    return (
        preset.exaggeration,
        preset.cfg_weight,
        preset.temperature,
        preset.repetition_penalty,
        preset.min_p,
        preset.top_p,
        preset.speech_speed,
    )


def preset_dict(name: str) -> dict[str, float]:
    return asdict(PRESETS.get(name, PRESETS["Natural"]))
