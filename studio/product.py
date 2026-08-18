from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .language import detect_latin_language, detect_script_language, script_looks_arabic
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


def resolve_language(language_ui: str | None, script: str) -> str:
    detected = detect_script_language(script)
    if language_ui and language_ui != "Auto":
        # English is the value left behind when an English-only model hides the language
        # control. If the script is clearly supported non-English text, do not let that
        # stale hidden value force Multilingual to synthesize with the wrong language id.
        if language_ui == "English" and detected and detected != "English":
            return detected
        return language_ui
    return detected or "English"


def _routing_language(language_ui: str | None, script: str) -> str:
    """Return the safest language for model routing/compatibility decisions.

    The visible language control can legitimately be stale or hidden after switching
    models. Obvious script evidence therefore wins for routing so Arabic, Russian,
    Spanish, etc. can never be sent to an English-only model by accident.
    """
    detected = detect_script_language(script)
    if detected and detected != "English":
        return detected
    return resolve_language(language_ui, script)


def resolve_model_id(
    model_ui: str | None,
    language_ui: str | None,
    script: str,
    profile: ProductSystemProfile,
) -> str:
    explicit = model_id_from_ui_name(model_ui)
    language = _routing_language(language_ui, script)
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
    language = _routing_language(language_ui, script)
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
