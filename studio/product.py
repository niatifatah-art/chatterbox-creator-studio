from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .models import MODEL_SPECS, PARALINGUISTIC_TAGS

AUTO_MODEL = "Auto"

# Product-facing names deliberately describe the job, not the implementation.
MODEL_UI_NAMES: dict[str, str] = {
    "multilingual-v3": "Multilingual",
    "turbo": "Expressive",
    "nano": "Light",
}
MODEL_UI_DESCRIPTIONS: dict[str, str] = {
    "multilingual-v3": "Best for Arabic and other supported languages.",
    "turbo": "Fast English speech with expressive tags such as laughs and sighs.",
    "nano": "The lightest option and usually the best fit for CPU-only computers.",
}
MODEL_SELECTIONS: tuple[str, ...] = (AUTO_MODEL, *MODEL_UI_NAMES.values())

QUALITY_MODES: tuple[str, ...] = ("Fast", "Balanced", "Best")
COMPUTE_CHOICES: tuple[str, ...] = ("Auto", "GPU", "CPU")


@dataclass(frozen=True)
class ProductSystemProfile:
    compute: str = "cpu"
    ram_gb: float | None = None
    vram_gb: float | None = None


def model_id_from_ui_name(value: str | None) -> str | None:
    if not value or value == AUTO_MODEL:
        return None
    for model_id, display in MODEL_UI_NAMES.items():
        if value == display:
            return model_id
    # Accept technical names too so saved pre-v1.1 settings remain usable.
    for model_id, spec in MODEL_SPECS.items():
        if value in {model_id, spec.name, spec.short_name}:
            return model_id
    return None


def model_ui_name(model_id: str) -> str:
    return MODEL_UI_NAMES.get(model_id, MODEL_SPECS[model_id].short_name)


def model_detail(model_id: str) -> str:
    spec = MODEL_SPECS[model_id]
    friendly = model_ui_name(model_id)
    description = MODEL_UI_DESCRIPTIONS[model_id]
    languages = f"{len(spec.languages)} languages" if spec.capabilities.multilingual else "English"
    return f"**{friendly}** · {languages}  \n{description}"


def contains_expression_tag(text: str) -> bool:
    lowered = (text or "").lower()
    return any(tag.lower() in lowered for tag in PARALINGUISTIC_TAGS)


def script_looks_arabic(text: str) -> bool:
    """Conservative Arabic-script hint used only when Language is set to Auto."""
    letters = [char for char in (text or "") if char.isalpha()]
    if not letters:
        return False
    arabic = sum(1 for char in letters if "\u0600" <= char <= "\u06ff" or "\u0750" <= char <= "\u077f" or "\u08a0" <= char <= "\u08ff")
    return arabic / len(letters) >= 0.30


def resolve_language(language_ui: str | None, script: str) -> str:
    if language_ui and language_ui != "Auto":
        return language_ui
    if script_looks_arabic(script):
        return "Arabic"
    return "English"


def resolve_model_id(
    model_ui: str | None,
    language_ui: str | None,
    script: str,
    profile: ProductSystemProfile,
) -> str:
    explicit = model_id_from_ui_name(model_ui)
    language = resolve_language(language_ui, script)
    if explicit:
        if language != "English" and not MODEL_SPECS[explicit].capabilities.multilingual:
            raise ValueError(f"{model_ui} supports English only. Use Multilingual for {language}.")
        return explicit

    if language != "English":
        return "multilingual-v3"
    if contains_expression_tag(script):
        return "turbo"
    if profile.compute == "cpu":
        return "nano"
    if profile.vram_gb is not None and profile.vram_gb < 6:
        return "nano"
    return "turbo"


def compatible_models(language_ui: str | None, script: str) -> tuple[str, ...]:
    language = resolve_language(language_ui, script)
    if language == "English":
        return tuple(MODEL_SPECS)
    return tuple(model_id for model_id, spec in MODEL_SPECS.items() if spec.capabilities.multilingual)


def quality_policy(mode: str | None) -> dict[str, int | bool]:
    """Turn three human choices into reliability defaults.

    STT verification intentionally remains independent. It is expensive and belongs
    in Tools/Expert, not in the default generation path.
    """
    normalized = mode if mode in QUALITY_MODES else "Balanced"
    if normalized == "Fast":
        return {"quality_check": False, "auto_retries": 0, "best_of_n": 1}
    if normalized == "Best":
        return {"quality_check": True, "auto_retries": 0, "best_of_n": 2}
    return {"quality_check": True, "auto_retries": 0, "best_of_n": 1}


def safe_compare_order(model_ids: Iterable[str]) -> tuple[str, ...]:
    """Keep comparisons predictable and memory-safe by running one model at a time."""
    preferred = ("multilingual-v3", "turbo", "nano")
    requested = set(model_ids)
    return tuple(model_id for model_id in preferred if model_id in requested)


def human_model_status(installed: bool, loaded: bool, update_available: bool | None) -> str:
    if loaded:
        return "Ready · in memory"
    if not installed:
        return "Not installed"
    if update_available:
        return "Update available"
    return "Ready"
