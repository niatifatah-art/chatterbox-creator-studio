from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any, Callable

import gradio as gr

# The stable generation/storage/model controller remains in app.py for now. This file
# is the supported product surface and deliberately keeps model/provider details out
# of the normal creator workflow.
import app as core
from studio.model_profiles import capabilities_for, language_control_needed, profile_for
from studio.recipes import RecipeStore

ROOT = Path(__file__).resolve().parent
UI_CSS_PATH = ROOT / "assets" / "product_v111.css"
RECIPES = RecipeStore(core.DATA_DIR / "recipes.json")

MODEL_IDS = tuple(core.MODEL_SPECS)
MODEL_NAMES = {model_id: core.model_ui_name(model_id) for model_id in MODEL_IDS}
MODEL_ESTIMATES_GB = {"multilingual-v3": 5.9, "turbo": 4.0, "nano": 2.7}
MODEL_SHORT = {
    "multilingual-v3": "23 languages · best choice for Arabic and multilingual work",
    "turbo": "Fast expressive English · supports speech expression tags",
    "nano": "Lightweight English · friendly to CPU-only computers",
}


def _installed(model_id: str) -> bool:
    try:
        return bool(core.model_manager.status(model_id).installed)
    except Exception:
        return False


def _model_ui_for_id(model_id: str) -> str:
    return MODEL_NAMES.get(model_id, MODEL_NAMES["multilingual-v3"])


def _resolved_model(model_ui: str, language_ui: str, script: str, compute_preference: str) -> tuple[str, str]:
    model_id, language_name, _, _ = core._resolve_choice(model_ui, language_ui, script or "", compute_preference)
    return model_id, language_name


def _effective_language(model_ui: str, language_ui: str) -> str:
    explicit = core.model_id_from_ui_name(model_ui)
    return language_ui if language_control_needed(explicit) else "English"


def _friendly_download_callback(progress: gr.Progress, model_id: str):
    label = MODEL_NAMES[model_id]

    def callback(current: int, total: int | None, _upstream_desc: str) -> None:
        if total and total > 0:
            core._gr_progress(progress, f"Downloading {label}", current, total, unit="file")
        else:
            core._gr_progress(progress, f"Preparing {label}…", None, None, unit="file")

    return callback


def _model_card_text(model_id: str) -> str:
    status = core.model_manager.status(model_id)
    if status.installed:
        size = f"{status.size_gb:.2f} GB" if status.size_gb else "installed"
        state = f"<span class='state-ready'>● Installed</span> · {size}"
    else:
        estimate = MODEL_ESTIMATES_GB.get(model_id)
        size = f"≈ {estimate:.1f} GB" if estimate else "download required"
        state = f"<span class='state-missing'>○ Not installed</span> · {size}"
    loaded = " · <b>In memory</b>" if core.engine.loaded_model_id == model_id else ""
    return (
        f"<div class='model-card-copy'><div class='model-card-title'>{MODEL_NAMES[model_id]}</div>"
        f"<div class='model-card-desc'>{MODEL_SHORT[model_id]}</div>"
        f"<div class='model-card-state'>{state}{loaded}</div></div>"
    )


def _model_card_updates(model_id: str):
    installed = _installed(model_id)
    return (
        _model_card_text(model_id),
        gr.update(visible=not installed, interactive=True),
        gr.update(visible=installed, interactive=True),
        gr.update(visible=installed, interactive=True),
        gr.update(visible=installed, interactive=True),
    )


def refresh_all_model_cards():
    values: list[Any] = []
    for model_id in MODEL_IDS:
        values.extend(_model_card_updates(model_id))
    return tuple(values)


def filter_model_cards(query: str, availability: str):
    needle = (query or "").strip().lower()
    updates = []
    for model_id in MODEL_IDS:
        installed = _installed(model_id)
        searchable = f"{MODEL_NAMES[model_id]} {MODEL_SHORT[model_id]} {model_id}".lower()
        text_match = not needle or needle in searchable
        filter_match = (
            availability == "All"
            or (availability == "Installed" and installed)
            or (availability == "Available" and not installed)
        )
        updates.append(gr.update(visible=text_match and filter_match))
    return tuple(updates)


def install_model_by_id(model_id: str, offline_mode: bool, progress=gr.Progress()):
    if offline_mode:
        raise gr.Error("Turn off Offline mode before downloading a model.")
    try:
        status = core.model_manager.download(
            model_id,
            offline=False,
            progress=_friendly_download_callback(progress, model_id),
        )
        progress(None)
        return f"✅ **{MODEL_NAMES[model_id]}** is ready · {status.size_gb:.2f} GB."
    except Exception as exc:
        progress(None)
        raise gr.Error(core._friendly_error(exc)) from exc


def use_model_by_id(model_id: str, compute_preference: str, offline_mode: bool, progress=gr.Progress()):
    try:
        core._configure_engine(model_id, compute_preference, offline_mode, False, progress)
        core.engine.load_model(model_id, progress_callback=core._engine_progress(progress))
        progress(None)
        return f"✅ **{MODEL_NAMES[model_id]}** is loaded and ready."
    except Exception as exc:
        progress(None)
        raise gr.Error(core._friendly_error(exc)) from exc


def update_model_by_id(model_id: str, offline_mode: bool, progress=gr.Progress()):
    if offline_mode:
        raise gr.Error("Turn off Offline mode before updating a model.")
    try:
        status = core.model_manager.update(
            model_id,
            offline=False,
            progress=_friendly_download_callback(progress, model_id),
        )
        if core.engine.loaded_model_id == model_id:
            core.engine.unload()
        progress(None)
        return f"✅ **{MODEL_NAMES[model_id]}** updated · {status.size_gb:.2f} GB."
    except Exception as exc:
        progress(None)
        raise gr.Error(core._friendly_error(exc)) from exc


def remove_model_by_id(model_id: str):
    if core.engine.loaded_model_id == model_id:
        core.engine.unload()
    _, note = core.remove_model_ui(_model_ui_for_id(model_id))
    return note


def _model_install_action(model_id: str) -> Callable:
    def action(offline_mode: bool, progress=gr.Progress()):
        return install_model_by_id(model_id, offline_mode, progress)
    return action


def _model_use_action(model_id: str) -> Callable:
    def action(compute_preference: str, offline_mode: bool, progress=gr.Progress()):
        return use_model_by_id(model_id, compute_preference, offline_mode, progress)
    return action


def _model_update_action(model_id: str) -> Callable:
    def action(offline_mode: bool, progress=gr.Progress()):
        return update_model_by_id(model_id, offline_mode, progress)
    return action


def _model_remove_action(model_id: str) -> Callable:
    def action():
        return remove_model_by_id(model_id)
    return action


def _compare_data(language_ui: str, script: str, current: list[str] | None = None):
    compatible = tuple(core.safe_compare_order(core.compatible_models(language_ui, script or "")))
    choices: list[tuple[str, str]] = []
    installed_ids: list[str] = []
    for model_id in compatible:
        ready = _installed(model_id)
        if ready:
            installed_ids.append(model_id)
        estimate = MODEL_ESTIMATES_GB.get(model_id)
        state = "Ready" if ready else (f"Install first · ≈{estimate:.1f} GB" if estimate else "Install first")
        choices.append((f"{MODEL_NAMES[model_id]} · {state}", model_id))

    selected = installed_ids.copy() if current is None else [mid for mid in current if mid in compatible]
    language = core.resolve_language(language_ui, script or "")
    if len(compatible) < 2:
        note = f"**{language}** currently has one compatible model, so there is nothing useful to compare."
        enabled = False
    elif len(selected) < 2:
        note = "Choose at least **two installed models**. Compare never downloads anything without you choosing it first."
        enabled = False
    else:
        missing = [MODEL_NAMES[mid] for mid in selected if not _installed(mid)]
        if missing:
            note = "Install **" + ", ".join(missing) + "** first."
            enabled = False
        else:
            note = f"**{len(selected)} models selected** · results appear one by one while the app protects memory."
            enabled = True
    return choices, selected, note, enabled


def refresh_compare_picker(language_ui, script, current):
    choices, selected, note, enabled = _compare_data(language_ui, script, current or [])
    return gr.update(choices=choices, value=selected), note, gr.update(interactive=enabled)


def compare_selection_note(selected, language_ui, script):
    _, selected, note, enabled = _compare_data(language_ui, script, selected or [])
    return note, gr.update(interactive=enabled)


def _resolve_profile(model_ui: str, language_ui: str, script: str, compute_preference: str, style: str):
    try:
        model_id, _ = _resolved_model(model_ui, language_ui, script, compute_preference)
    except Exception:
        model_id = core.model_id_from_ui_name(model_ui) or "multilingual-v3"
    return model_id, profile_for(model_id, style)


def update_sound_controls(model_ui, language_ui, script, compute_preference, style):
    model_id, profile = _resolve_profile(model_ui, language_ui, script, compute_preference, style)
    caps = capabilities_for(model_id)
    explicit_id = core.model_id_from_ui_name(model_ui)
    show_language = language_control_needed(explicit_id)
    effective_language = language_ui if show_language else "English"
    return (
        gr.update(visible=show_language, value=effective_language),
        gr.update(value=profile.exaggeration),
        gr.update(value=profile.cfg_weight),
        gr.update(value=profile.temperature),
        gr.update(value=profile.repetition_penalty),
        gr.update(value=profile.min_p),
        gr.update(value=profile.top_p),
        gr.update(value=profile.top_k),
        gr.update(value=profile.speech_speed),
        gr.update(visible=caps.exaggeration or caps.cfg_weight or caps.min_p),
        gr.update(visible=caps.top_k),
        gr.update(visible=caps.expressive_tags),
        core._model_state_line(model_ui, effective_language, script, compute_preference),
    )


def update_batch_language(model_ui: str, current_language: str):
    model_id = core.model_id_from_ui_name(model_ui)
    show = language_control_needed(model_id)
    return gr.update(visible=show, value=current_language if show else "English")


def _effective_tuning(
    model_id: str,
    style: str,
    recommended: bool,
    exaggeration: float,
    cfg_weight: float,
    temperature: float,
    repetition_penalty: float,
    min_p: float,
    top_p: float,
    top_k: int,
    speech_speed: float,
):
    if recommended:
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
    return (
        float(exaggeration), float(cfg_weight), float(temperature), float(repetition_penalty),
        float(min_p), float(top_p), int(top_k), float(speech_speed),
    )


def _recipe_payload(
    audio_path: str | None,
    metadata_path: str | None,
    voice_name: str | None,
    model_id: str,
    language_name: str,
    style: str,
    speech_speed: float,
    generation: dict[str, Any],
    finishing: dict[str, Any],
) -> dict[str, Any] | None:
    if not audio_path:
        return None
    actual_seed = int(generation.get("seed", -1))
    if metadata_path:
        try:
            metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
            actual_seed = int(metadata.get("seed", actual_seed))
        except Exception:
            pass
    return {
        "audio_path": str(audio_path),
        "voice": voice_name,
        "model_id": model_id,
        "language": language_name,
        "style": style,
        "speech_speed": float(speech_speed),
        "seed": actual_seed,
        "generation": dict(generation),
        "finishing": dict(finishing),
    }


def _recipe_choices():
    return [(item.name, item.id) for item in RECIPES.list()]


def save_recipe(recipe_state: dict | None, name: str | None):
    if not recipe_state:
        return "Generate a sound first.", gr.update()
    recipe = RECIPES.save(
        name=(name or "Favorite sound"),
        voice=recipe_state.get("voice"),
        model_id=recipe_state["model_id"],
        language=recipe_state["language"],
        style=recipe_state["style"],
        speech_speed=recipe_state["speech_speed"],
        seed=recipe_state["seed"],
        generation=recipe_state.get("generation") or {},
        finishing=recipe_state.get("finishing") or {},
    )
    return f"♡ Saved **{recipe.name}**.", gr.update(choices=_recipe_choices(), value=recipe.id)


def delete_recipe(recipe_id: str | None):
    if not recipe_id or not RECIPES.delete(recipe_id):
        return gr.update(), "Choose a saved sound first."
    choices = _recipe_choices()
    return gr.update(choices=choices, value=choices[0][1] if choices else None), "Saved sound deleted."


def apply_recipe(recipe_id: str | None):
    recipe = RECIPES.get(recipe_id)
    if recipe is None:
        return (gr.update(),) * 12 + ("Choose a saved sound first.",)
    generation = recipe.generation or {}
    return (
        gr.update(value=recipe.voice),
        gr.update(value=_model_ui_for_id(recipe.model_id)),
        gr.update(value=recipe.language),
        gr.update(value=recipe.style),
        gr.update(value=recipe.speech_speed),
        gr.update(value=float(generation.get("exaggeration", 0.5))),
        gr.update(value=float(generation.get("cfg_weight", 0.5))),
        gr.update(value=float(generation.get("temperature", 0.8))),
        gr.update(value=float(generation.get("repetition_penalty", 1.2))),
        gr.update(value=float(generation.get("min_p", 0.05))),
        gr.update(value=float(generation.get("top_p", 1.0))),
        gr.update(value=int(generation.get("top_k", 1000))),
        f"✅ Loaded **{recipe.name}**. You can still change anything before generating.",
    )


def _generate_core(
    script, project_id, voice_name, model_ui, language_ui, style, speech_speed,
    compute_preference, quality_mode, offline_mode, recommended_tuning,
    exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, top_k,
    raw_mode, smart_chunking, max_chars, chunk_gap_seconds, seed,
    normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls,
    collapse_punctuation, normalize_whitespace, quality_check, verify_stt,
    whisper_model, verification_threshold, auto_retries, best_of_n,
    trim_silence, peak_normalize, fade_ms,
    *, allow_download: bool, progress: gr.Progress,
):
    effective_language_ui = _effective_language(model_ui, language_ui)
    model_id, language_name = _resolved_model(model_ui, effective_language_ui, script or "", compute_preference)
    if not _installed(model_id) and not allow_download:
        return None, model_id, language_name, None
    if not _installed(model_id):
        if offline_mode:
            raise gr.Error("Turn off Offline mode before downloading this model.")
        core.model_manager.download(model_id, offline=False, progress=_friendly_download_callback(progress, model_id))

    ex, cfg, temp, rep, mp, tp, tk, speed = _effective_tuning(
        model_id, style, recommended_tuning, exaggeration, cfg_weight, temperature,
        repetition_penalty, min_p, top_p, top_k, speech_speed,
    )
    result = core.generate_audio(
        script, project_id, voice_name, model_ui, effective_language_ui, style, speed,
        compute_preference, quality_mode, offline_mode, False,
        ex, cfg, temp, rep, mp, tp, tk,
        raw_mode, smart_chunking, max_chars, chunk_gap_seconds, seed,
        normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls,
        collapse_punctuation, normalize_whitespace, quality_check, verify_stt,
        whisper_model, verification_threshold, auto_retries, best_of_n,
        trim_silence, peak_normalize, fade_ms, progress,
    )
    recipe = _recipe_payload(
        result[0], result[2], voice_name, model_id, language_name, style, speed,
        {
            "exaggeration": ex,
            "cfg_weight": cfg,
            "temperature": temp,
            "repetition_penalty": rep,
            "min_p": mp,
            "top_p": tp,
            "top_k": tk,
            "seed": seed,
        },
        {"trim_silence": bool(trim_silence), "peak_normalize": bool(peak_normalize), "fade_ms": int(fade_ms)},
    )
    return result, model_id, language_name, recipe


def generate_product(
    script, project_id, voice_name, model_ui, language_ui, style, speech_speed,
    compute_preference, quality_mode, offline_mode, recommended_tuning,
    exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, top_k,
    raw_mode, smart_chunking, max_chars, chunk_gap_seconds, seed,
    normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls,
    collapse_punctuation, normalize_whitespace, quality_check, verify_stt,
    whisper_model, verification_threshold, auto_retries, best_of_n,
    trim_silence, peak_normalize, fade_ms, progress=gr.Progress(),
):
    try:
        result, model_id, _, recipe = _generate_core(
            script, project_id, voice_name, model_ui, language_ui, style, speech_speed,
            compute_preference, quality_mode, offline_mode, recommended_tuning,
            exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, top_k,
            raw_mode, smart_chunking, max_chars, chunk_gap_seconds, seed,
            normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls,
            collapse_punctuation, normalize_whitespace, quality_check, verify_stt,
            whisper_model, verification_threshold, auto_retries, best_of_n,
            trim_silence, peak_normalize, fade_ms, allow_download=False, progress=progress,
        )
        if result is None:
            estimate = MODEL_ESTIMATES_GB.get(model_id)
            size = f" · about {estimate:.1f} GB" if estimate else ""
            return (
                gr.update(), gr.update(), gr.update(),
                f"**{MODEL_NAMES[model_id]}** is not installed yet.",
                gr.update(), gr.update(), gr.update(),
                core._model_state_line(model_ui, _effective_language(model_ui, language_ui), script, compute_preference),
                gr.update(visible=True),
                f"### Download {MODEL_NAMES[model_id]}?\nThis model is needed for your current choice{size}. Nothing downloads until you approve it.",
                model_id, gr.update(interactive=False), None,
            )
        return (*result, gr.update(visible=False), "", None, gr.update(interactive=True), recipe)
    except Exception as exc:
        progress(None)
        raise gr.Error(core._friendly_error(exc)) from exc


def download_and_generate_product(
    pending_model_id,
    script, project_id, voice_name, model_ui, language_ui, style, speech_speed,
    compute_preference, quality_mode, offline_mode, recommended_tuning,
    exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, top_k,
    raw_mode, smart_chunking, max_chars, chunk_gap_seconds, seed,
    normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls,
    collapse_punctuation, normalize_whitespace, quality_check, verify_stt,
    whisper_model, verification_threshold, auto_retries, best_of_n,
    trim_silence, peak_normalize, fade_ms, progress=gr.Progress(),
):
    if not pending_model_id:
        raise gr.Error("There is no pending model download.")
    try:
        result, _, _, recipe = _generate_core(
            script, project_id, voice_name, model_ui, language_ui, style, speech_speed,
            compute_preference, quality_mode, offline_mode, recommended_tuning,
            exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, top_k,
            raw_mode, smart_chunking, max_chars, chunk_gap_seconds, seed,
            normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls,
            collapse_punctuation, normalize_whitespace, quality_check, verify_stt,
            whisper_model, verification_threshold, auto_retries, best_of_n,
            trim_silence, peak_normalize, fade_ms, allow_download=True, progress=progress,
        )
        if result is None:
            raise RuntimeError("The model could not be prepared.")
        return (*result, gr.update(visible=False), "", None, gr.update(interactive=True), recipe)
    except Exception as exc:
        progress(None)
        raise gr.Error(core._friendly_error(exc)) from exc


def cancel_model_prompt():
    return gr.update(visible=False), "", None


def _compare_outputs(results: dict[str, str | None], recipes: dict[str, dict | None], status: str):
    values: list[Any] = []
    for model_id in ("multilingual-v3", "turbo", "nano"):
        path = results.get(model_id)
        values.extend([
            gr.update(value=path, visible=path is not None),
            gr.update(value=path, visible=path is not None),
            gr.update(visible=path is not None, interactive=path is not None),
            recipes.get(model_id),
        ])
    values.append(status)
    return tuple(values)


def compare_selected_models(
    selected_models, script, project_id, voice_name, language_ui, style, speech_speed,
    compute_preference, quality_mode, offline_mode, recommended_tuning,
    exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, top_k,
    raw_mode, smart_chunking, max_chars, chunk_gap_seconds, seed,
    normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls,
    collapse_punctuation, normalize_whitespace, quality_check, verify_stt,
    whisper_model, verification_threshold, auto_retries, best_of_n,
    trim_silence, peak_normalize, fade_ms, progress=gr.Progress(),
):
    if not (script or "").strip():
        raise gr.Error("Write something first.")
    compatible = set(core.compatible_models(language_ui, script))
    requested = tuple(mid for mid in core.safe_compare_order(selected_models or []) if mid in compatible)
    if len(requested) < 2:
        raise gr.Error("Choose at least two compatible installed models.")
    missing = [MODEL_NAMES[mid] for mid in requested if not _installed(mid)]
    if missing:
        raise gr.Error("Install " + ", ".join(missing) + " first. Compare never downloads automatically.")

    language_name = core.resolve_language(language_ui, script)
    results = {model_id: None for model_id in MODEL_IDS}
    recipes: dict[str, dict | None] = {model_id: None for model_id in MODEL_IDS}
    completed: list[str] = []
    total = len(requested)
    try:
        yield _compare_outputs(results, recipes, f"**Starting comparison** · {total} selected models")
        for index, model_id in enumerate(requested, 1):
            label = MODEL_NAMES[model_id]
            yield _compare_outputs(results, recipes, f"**Comparing {index}/{total}** · preparing **{label}**…")
            try:
                core._configure_engine(model_id, compute_preference, offline_mode, False, progress)
                ex, cfg, temp, rep, mp, tp, tk, speed = _effective_tuning(
                    model_id, style, recommended_tuning, exaggeration, cfg_weight, temperature,
                    repetition_penalty, min_p, top_p, top_k, speech_speed,
                )
                kwargs = core._generation_kwargs(
                    voice_name, model_id, language_name, ex, cfg, temp, rep, mp, tp, tk,
                    speed, raw_mode, smart_chunking, max_chars, chunk_gap_seconds, seed,
                    progress_callback=core._engine_progress(progress, prefix=f"{label} · "),
                )
                reliable = core.generate_reliably(
                    core.engine,
                    script,
                    policy=core._policy(
                        raw_mode, normalize_unicode, normalize_punctuation, normalize_numbers,
                        replace_urls, collapse_punctuation, normalize_whitespace, quality_check,
                        verify_stt, whisper_model, verification_threshold, auto_retries, best_of_n,
                    ),
                    **kwargs,
                )
                selected = reliable.selected
                final_audio = core._finish_audio(selected.result.audio_path, trim_silence, peak_normalize, fade_ms)
                results[model_id] = str(final_audio)
                recipes[model_id] = _recipe_payload(
                    str(final_audio), str(selected.result.metadata_path), voice_name, model_id,
                    language_name, style, speed,
                    {
                        "exaggeration": ex, "cfg_weight": cfg, "temperature": temp,
                        "repetition_penalty": rep, "min_p": mp, "top_p": tp,
                        "top_k": tk, "seed": seed,
                    },
                    {"trim_silence": bool(trim_silence), "peak_normalize": bool(peak_normalize), "fade_ms": int(fade_ms)},
                )
                completed.append(label)
                if project_id:
                    try:
                        core.projects.add_take(project_id, final_audio, selected.result.metadata_path, segment_id=f"compare-{model_id}")
                    except Exception:
                        pass
                remaining = total - index
                yield _compare_outputs(
                    results, recipes,
                    f"✅ **{label} ready**" + (f" · {remaining} remaining" if remaining else ""),
                )
            except Exception as exc:
                completed.append(f"{label} failed")
                yield _compare_outputs(results, recipes, f"⚠️ **{label}** could not finish: {core._friendly_error(exc)}")
            finally:
                core.engine.unload()
        progress(None)
        if any(results.values()):
            yield _compare_outputs(results, recipes, "✅ **Comparison ready** · " + " · ".join(completed))
        else:
            raise gr.Error("None of the selected models produced a result.")
    finally:
        progress(None)
        core.engine.unload()


def _compare_busy():
    return gr.update(value="Comparing…", interactive=False), gr.update(visible=True)


def _compare_idle(selected):
    return gr.update(value="Compare", interactive=len(selected or []) >= 2), gr.update(visible=False)


def _generate_busy():
    return gr.update(value="Generating…", interactive=False)


def _generate_idle():
    return gr.update(value="Generate", interactive=True)


def install_speech_tools_inline(progress=gr.Progress()):
    status = core.install_speech_tools(progress)
    return status, gr.update(visible=False)


def speech_tools_available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def _initial_script() -> str:
    if core.initial_project:
        project = core.projects.load(core.initial_project)
        if project and (project.get("script") or "").strip():
            return str(project.get("script"))
    return "Type what you want to hear."


def _insert_js(token: str) -> str:
    escaped = token.replace("'", "\\'")
    return f"""
    (text) => {{
      const area = document.querySelector('#script-box textarea');
      if (!area) return text || '';
      const original = text || '';
      const start = area.selectionStart ?? original.length;
      const end = area.selectionEnd ?? start;
      const token = '{escaped}';
      const before = original.slice(0, start);
      const after = original.slice(end);
      const left = before && !/\\s$/.test(before) ? ' ' : '';
      const right = after && !/^\\s/.test(after) ? ' ' : '';
      const updated = before + left + token + right + after;
      const cursor = before.length + left.length + token.length + right.length;
      setTimeout(() => {{
        const current = document.querySelector('#script-box textarea');
        if (current) {{ current.focus(); current.setSelectionRange(cursor, cursor); }}
      }}, 0);
      return updated;
    }}
    """


available_voices = core._voice_choices()
initial_voice = core.settings.get("voice") if core.settings.get("voice") in available_voices else (available_voices[0] if available_voices else None)
project_ids = core._project_choices()
initial_project = core.settings.get("project") if core.settings.get("project") in project_ids else (project_ids[0] if project_ids else None)
initial_model_ui = core.settings.get("model_selection") if core.settings.get("model_selection") in core.MODEL_SELECTIONS else core.AUTO_MODEL
initial_language_ui = core.settings.get("language_selection") if core.settings.get("language_selection") in core.LANGUAGE_CHOICES else "Auto"
initial_style = core.settings.get("preset") if core.settings.get("preset") in core.PRESETS else "Creator"
initial_quality = core.settings.get("generation_quality") if core.settings.get("generation_quality") in core.QUALITY_MODES else "Balanced"
initial_compute = core.settings.get("compute_preference") if core.settings.get("compute_preference") in core.COMPUTE_CHOICES else "Auto"
initial_quality_policy = core.quality_policy(initial_quality)
initial_script = _initial_script()
initial_model_id, initial_profile = _resolve_profile(initial_model_ui, initial_language_ui, initial_script, initial_compute, initial_style)
initial_caps = capabilities_for(initial_model_id)
compare_choices, compare_selected, compare_note, compare_enabled = _compare_data(initial_language_ui, initial_script, None)
recipe_choices = _recipe_choices()

with gr.Blocks(title="Creator Studio", analytics_enabled=False) as demo:
    pending_model = gr.State(None)
    last_recipe_state = gr.State(None)
    compare_recipe_v3 = gr.State(None)
    compare_recipe_turbo = gr.State(None)
    compare_recipe_nano = gr.State(None)
    compute_state = gr.State(initial_compute)
    quality_state = gr.State(initial_quality)
    offline_state = gr.State(bool(core.settings.get("offline_mode", False)))
    no_auto_download = gr.State(False)

    gr.HTML(
        """
        <header class="app-header">
          <div class="brand-mark" aria-hidden="true">◌</div>
          <div class="brand-copy"><strong>Creator Studio</strong><span>Local voice workspace</span></div>
          <div class="local-badge">Local-first</div>
        </header>
        """
    )

    with gr.Tabs(elem_id="product-nav"):
        with gr.Tab("Create"):
            with gr.Row(equal_height=False, elem_classes="create-layout"):
                with gr.Column(scale=8, elem_classes="workspace-card create-main"):
                    with gr.Row(elem_classes="project-bar"):
                        project_dropdown = gr.Dropdown(label="Project", choices=project_ids, value=initial_project, allow_custom_value=False, scale=5)
                        project_refresh = gr.Button("↻", size="sm", scale=0, min_width=44, elem_id="project-refresh")

                    script = gr.Textbox(
                        label="Script", value=initial_script, lines=14, max_lines=30,
                        placeholder="Write or paste what you want to hear…", elem_id="script-box",
                    )
                    with gr.Row(elem_classes="quick-row"):
                        pause_025 = gr.Button("+ 0.25s", size="sm", variant="secondary")
                        pause_05 = gr.Button("+ 0.5s", size="sm", variant="secondary")
                        pause_1 = gr.Button("+ 1s", size="sm", variant="secondary")
                        pause_2 = gr.Button("+ 2s", size="sm", variant="secondary")

                    with gr.Group(visible=initial_caps.expressive_tags, elem_id="tag-tools") as tag_group:
                        gr.HTML("<span class='micro-label'>Expression tags</span>")
                        with gr.Row(elem_classes="tag-row"):
                            tag_buttons = [(gr.Button(tag, size="sm", variant="secondary"), tag) for tag in core.PARALINGUISTIC_TAGS]

                    with gr.Row(elem_classes="primary-actions"):
                        generate_btn = gr.Button("Generate", variant="primary", size="lg", elem_id="generate-btn")
                        compare_btn = gr.Button("Compare", variant="secondary", size="lg", elem_id="compare-btn", interactive=compare_enabled)
                        compare_cancel = gr.Button("Stop", variant="secondary", size="lg", visible=False, elem_id="compare-cancel")

                    with gr.Group(visible=False, elem_classes="download-confirm") as download_confirm:
                        download_confirm_text = gr.Markdown()
                        with gr.Row():
                            download_generate_btn = gr.Button("Download & generate", variant="primary")
                            download_cancel_btn = gr.Button("Cancel", variant="secondary")

                    create_status = gr.Markdown("Ready when you are.", elem_classes="status-line", elem_id="create-status")
                    output_audio = gr.Audio(label="Result", interactive=False, format="wav", elem_classes="result-audio")
                    with gr.Row(elem_classes="result-actions"):
                        output_download = gr.DownloadButton("Download audio", variant="secondary")
                        save_result_btn = gr.Button("♡ Save sound", variant="secondary", interactive=False)
                    recipe_name = gr.Textbox(label="Saved sound name", placeholder="e.g. Warm narrator")
                    recipe_status = gr.Markdown()

                    with gr.Accordion("Details", open=False):
                        generation_details = gr.Markdown("Generation details appear here.")
                        metadata_download = gr.DownloadButton("Download generation details", variant="secondary")
                        with gr.Row():
                            project_takes = gr.Dropdown(label="Project takes", choices=[])
                            takes_refresh = gr.Button("Refresh", size="sm")
                            take_load = gr.Button("Load", size="sm")

                    with gr.Accordion("Compare voices", open=False, elem_id="compare-panel"):
                        compare_picker = gr.CheckboxGroup(
                            label="Models to compare", choices=compare_choices, value=compare_selected,
                            info="Select exactly what you want to hear. Missing models are never downloaded automatically.",
                            elem_id="compare-model-picker", elem_classes="choice-pills",
                        )
                        compare_status = gr.Markdown(compare_note, elem_classes="status-line", elem_id="compare-status")
                        with gr.Row(elem_classes="compare-results-row"):
                            with gr.Column(elem_classes="compare-card"):
                                gr.Markdown("#### Multilingual")
                                compare_v3_audio = gr.Audio(interactive=False, visible=False, label="Multilingual")
                                with gr.Row():
                                    compare_v3_download = gr.DownloadButton("Download", visible=False)
                                    compare_v3_save = gr.Button("♡ Save", visible=False)
                            with gr.Column(elem_classes="compare-card"):
                                gr.Markdown("#### Expressive")
                                compare_turbo_audio = gr.Audio(interactive=False, visible=False, label="Expressive")
                                with gr.Row():
                                    compare_turbo_download = gr.DownloadButton("Download", visible=False)
                                    compare_turbo_save = gr.Button("♡ Save", visible=False)
                            with gr.Column(elem_classes="compare-card"):
                                gr.Markdown("#### Light")
                                compare_nano_audio = gr.Audio(interactive=False, visible=False, label="Light")
                                with gr.Row():
                                    compare_nano_download = gr.DownloadButton("Download", visible=False)
                                    compare_nano_save = gr.Button("♡ Save", visible=False)

                with gr.Column(scale=4, elem_classes="control-card create-sidebar"):
                    gr.Markdown("### Voice")
                    voice_dropdown = gr.Dropdown(label="Voice", choices=available_voices, value=initial_voice, allow_custom_value=False, elem_id="voice-picker")
                    with gr.Accordion("+ Add a voice", open=not bool(available_voices)):
                        quick_voice_audio = gr.Audio(label="Drop a recording or record now", sources=["upload", "microphone"], type="filepath", format="wav")
                        quick_voice_name = gr.Textbox(label="Name", placeholder="My voice")
                        quick_voice_save = gr.Button("Save & use", variant="primary")
                        quick_voice_status = gr.Markdown("A clean 8–15 second recording usually works well.")

                    gr.Markdown("### Sound")
                    model_ui = gr.Dropdown(
                        label="Model", choices=list(core.MODEL_SELECTIONS), value=initial_model_ui,
                        allow_custom_value=False, info="Auto chooses a sensible model for the text and this computer.",
                        elem_id="model-picker",
                    )
                    language_ui = gr.Dropdown(
                        label="Language", choices=core.LANGUAGE_CHOICES, value=initial_language_ui,
                        visible=language_control_needed(core.model_id_from_ui_name(initial_model_ui)),
                        allow_custom_value=False, elem_id="language-picker",
                    )
                    style = gr.Dropdown(label="Style", choices=list(core.PRESETS), value=initial_style)
                    speech_speed = gr.Slider(0.75, 1.25, value=initial_profile.speech_speed, step=0.01, label="Speed")
                    model_state = gr.Markdown(
                        core._model_state_line(initial_model_ui, initial_language_ui, initial_script, initial_compute),
                        elem_classes="model-choice-note", elem_id="model-state",
                    )

                    gr.Markdown("### Saved sounds")
                    saved_recipe = gr.Dropdown(label="Recipe", choices=recipe_choices, value=recipe_choices[0][1] if recipe_choices else None)
                    with gr.Row():
                        apply_recipe_btn = gr.Button("Use saved sound")
                        delete_recipe_btn = gr.Button("Delete", variant="stop")
                    saved_recipe_status = gr.Markdown("Save a result you love and reuse the exact sound later.")
                    gr.HTML("<div class='soft-note'>Most technical decisions are automatic. Fine controls live in <b>Settings → Expert</b>.</div>")

        with gr.Tab("Library"):
            with gr.Tabs():
                with gr.Tab("Voices"):
                    with gr.Row(equal_height=False):
                        with gr.Column(scale=2, elem_classes="workspace-card"):
                            library_voice_audio = gr.Audio(label="Add a voice", sources=["upload", "microphone"], type="filepath", format="wav")
                            library_voice_name = gr.Textbox(label="Name", placeholder="Narrator")
                            library_voice_save = gr.Button("Save voice", variant="primary")
                            library_voice_refresh = gr.Button("Refresh")
                            gr.HTML("<div class='soft-note'>Starter voice packs will live here later. Only voices with clear redistribution rights will be bundled.</div>")
                        with gr.Column(scale=3, elem_classes="workspace-card"):
                            voice_manage = gr.Dropdown(label="Your voices", choices=available_voices, value=initial_voice)
                            voice_preview = gr.Audio(label="Preview", interactive=False)
                            voice_inspection = gr.Markdown("Choose a voice to see a simple health check.")
                            voice_inspect = gr.Button("Check recording")
                            rename_voice_name = gr.Textbox(label="New name")
                            with gr.Row():
                                rename_voice_btn = gr.Button("Rename")
                                duplicate_voice_btn = gr.Button("Duplicate")
                                delete_voice_btn = gr.Button("Delete", variant="stop")
                            voice_manage_status = gr.Markdown()

                with gr.Tab("Projects"):
                    with gr.Row(equal_height=False):
                        with gr.Column(scale=2, elem_classes="workspace-card"):
                            project_name = gr.Textbox(label="Project name", placeholder="Video 01")
                            create_project_btn = gr.Button("Create project", variant="primary")
                            load_project_btn = gr.Button("Open selected project")
                            save_project_btn = gr.Button("Save now")
                            delete_project_btn = gr.Button("Delete project", variant="stop")
                            project_status = gr.Markdown("Projects keep scripts, choices, and takes together.")
                        with gr.Column(scale=3, elem_classes="workspace-card"):
                            gr.Markdown("### Your work stays local")
                            gr.Markdown("Create a project when you want scripts, choices, and takes kept together. Changes to an open project are saved automatically.")

                with gr.Tab("History"):
                    with gr.Row(equal_height=False):
                        with gr.Column(scale=2, elem_classes="workspace-card"):
                            history = gr.Dropdown(label="Recent generations", choices=core.engine.recent_outputs())
                            history_refresh = gr.Button("Refresh")
                            history_load = gr.Button("Open")
                            history_delete = gr.Button("Delete", variant="stop")
                            history_note = gr.Markdown()
                        with gr.Column(scale=3, elem_classes="workspace-card"):
                            history_audio = gr.Audio(label="Preview", interactive=False)
                            history_download = gr.DownloadButton("Download audio")
                            history_metadata = gr.DownloadButton("Download details")

        with gr.Tab("Tools"):
            with gr.Tabs():
                with gr.Tab("Transcribe"):
                    with gr.Row(equal_height=False):
                        with gr.Column(scale=2, elem_classes="workspace-card"):
                            stt_audio = gr.Audio(label="Audio", sources=["upload", "microphone"], type="filepath")
                            stt_quality = gr.Radio(label="Quality", choices=list(core.QUALITY_MODES), value=initial_quality, elem_id="stt-quality", elem_classes="choice-pills")
                            stt_language = gr.Dropdown(label="Language", choices=core.LANGUAGE_CHOICES, value="Auto")
                            stt_btn = gr.Button("Transcribe", variant="primary", elem_id="transcribe-btn")
                            stt_install_btn = gr.Button("Install speech tools", visible=not speech_tools_available(), elem_id="install-stt-btn")
                            stt_status = gr.Markdown(core.speech_tools_status(), elem_classes="status-line")
                        with gr.Column(scale=3, elem_classes="workspace-card"):
                            stt_text = gr.Textbox(label="Transcript", lines=16, max_lines=30)
                            stt_segments = gr.Dataframe(headers=["start", "end", "text"], datatype=["number", "number", "str"], interactive=False, wrap=True, label="Timestamps")

                with gr.Tab("Batch & subtitles"):
                    batch_state = gr.State([])
                    with gr.Row(equal_height=False):
                        with gr.Column(scale=2, elem_classes="workspace-card"):
                            batch_file = gr.File(label="TXT / MD / CSV / JSON / SRT / VTT", type="filepath")
                            batch_parse = gr.Button("Read file")
                            batch_voice = gr.Dropdown(label="Voice", choices=available_voices, value=initial_voice)
                            batch_model = gr.Dropdown(label="Model", choices=list(core.MODEL_SELECTIONS), value=initial_model_ui)
                            batch_language = gr.Dropdown(
                                label="Language", choices=core.LANGUAGE_CHOICES, value=initial_language_ui,
                                visible=language_control_needed(core.model_id_from_ui_name(initial_model_ui)),
                            )
                            batch_fit = gr.Checkbox(label="Fit subtitle clips to cue timing", value=bool(core.settings["batch_fit_timing"]))
                            batch_stretch = gr.Slider(1.02, 1.5, value=float(core.settings["batch_max_stretch"]), step=0.01, label="Maximum safe timing adjustment")
                            batch_run = gr.Button("Generate batch", variant="primary")
                            batch_manifest = gr.DownloadButton("Download manifest")
                            batch_output_dir = gr.Textbox(label="Output folder", interactive=False)
                            batch_status = gr.Markdown()
                        with gr.Column(scale=4, elem_classes="workspace-card"):
                            batch_table = gr.Dataframe(
                                headers=["id", "text", "start", "end", "target duration"],
                                datatype=["str", "str", "number", "number", "number"],
                                interactive=False, wrap=True, label="Items",
                            )

        with gr.Tab("Models"):
            gr.Markdown("## Models")
            gr.Markdown("Install only what you want. Nothing changes or downloads silently.")
            with gr.Row(elem_classes="model-toolbar"):
                model_search = gr.Textbox(label="Search models", placeholder="Search by name, language or purpose…", scale=3)
                model_filter = gr.Radio(label="Show", choices=["All", "Installed", "Available"], value="All", elem_classes="choice-pills", scale=2)
            model_groups: list[Any] = []
            model_card_components: list[Any] = []
            model_buttons: dict[str, tuple[Any, Any, Any, Any]] = {}
            for model_id in MODEL_IDS:
                safe_id = model_id.replace("-", "_")
                with gr.Group(elem_classes="model-library-card", elem_id=f"model-card-{safe_id}") as card_group:
                    model_groups.append(card_group)
                    card_text = gr.HTML(_model_card_text(model_id))
                    with gr.Row():
                        install_btn = gr.Button("Download", variant="primary", visible=not _installed(model_id), elem_id=f"model-download-{safe_id}")
                        use_btn = gr.Button("Use", variant="primary", visible=_installed(model_id), elem_id=f"model-use-{safe_id}")
                        update_btn = gr.Button("Update", visible=_installed(model_id), elem_id=f"model-update-{safe_id}")
                        remove_btn = gr.Button("Remove", variant="stop", visible=_installed(model_id), elem_id=f"model-remove-{safe_id}")
                    model_buttons[model_id] = (install_btn, use_btn, update_btn, remove_btn)
                    model_card_components.extend([card_text, install_btn, use_btn, update_btn, remove_btn])
            model_action_status = gr.Markdown("Choose a model action above.", elem_classes="status-line")
            with gr.Row(equal_height=False):
                with gr.Column(scale=2, elem_classes="workspace-card"):
                    gr.Markdown("### This computer")
                    hardware_box = gr.Markdown(core.hardware_summary(core.hardware), elem_classes="hardware-card")
                    gr.Markdown("**Performance: Auto** uses the fastest backend this installation can actually use.")
                with gr.Column(scale=3, elem_classes="workspace-card"):
                    gr.Markdown("### How models work")
                    gr.Markdown("Downloaded models stay pinned to the version you chose. Updates happen only when you press Update. Removing a model never removes your voices or projects.")

        with gr.Tab("Settings"):
            with gr.Row(equal_height=False):
                with gr.Column(scale=2, elem_classes="workspace-card"):
                    gr.Markdown("## Simple defaults")
                    compute_preference = gr.Radio(label="Performance", choices=list(core.COMPUTE_CHOICES), value=initial_compute, info="Auto is recommended.", elem_classes="choice-pills")
                    quality_mode = gr.Radio(label="Generation quality", choices=list(core.QUALITY_MODES), value=initial_quality, elem_classes="choice-pills")
                    offline_mode = gr.Checkbox(label="Offline mode", value=bool(core.settings.get("offline_mode", False)), info="Blocks model downloads and update checks while enabled.")
                    gr.Markdown("### Audio finishing")
                    trim_silence = gr.Checkbox(label="Trim empty silence at the ends", value=bool(core.settings["trim_silence"]))
                    peak_normalize = gr.Checkbox(label="Normalize output level", value=bool(core.settings["peak_normalize"]))
                    fade_ms = gr.Slider(0, 250, value=int(core.settings["fade_ms"]), step=5, label="Short fade (ms)")
                    save_preferences = gr.Button("Save settings", variant="primary")
                    preferences_status = gr.Markdown()
                    gr.Markdown("### Speech tools")
                    speech_tools_note = gr.Markdown(core.speech_tools_status())
                    install_speech_btn = gr.Button("Install speech tools")

                with gr.Column(scale=3, elem_classes="workspace-card"):
                    with gr.Accordion("Expert", open=False):
                        recommended_tuning = gr.Checkbox(
                            label="Use recommended tuning for each model and style", value=True,
                            info="Recommended keeps each model on its own sensible defaults. Turn this off only when you want to tune technical controls yourself.",
                        )
                        raw_mode = gr.Checkbox(label="Raw model text path", value=bool(core.settings["raw_mode"]), info="Bypasses pause parsing, chunking, and text cleanup.")
                        smart_chunking = gr.Checkbox(label="Split long text automatically", value=bool(core.settings["smart_chunking"]))
                        max_chars = gr.Slider(80, 500, value=int(core.settings["max_chars"]), step=10, label="Target characters per chunk")
                        chunk_gap = gr.Slider(0.0, 1.0, value=float(core.settings["chunk_gap_seconds"]), step=0.01, label="Gap between chunks (seconds)")
                        seed = gr.Number(value=int(core.settings["seed"]), precision=0, label="Seed", info="-1 creates a fresh seed and records it.")

                        gr.Markdown("#### Voice generation")
                        with gr.Group(visible=initial_caps.exaggeration or initial_caps.cfg_weight or initial_caps.min_p) as multilingual_expert_group:
                            exaggeration = gr.Slider(0.0, 1.5, value=initial_profile.exaggeration, step=0.05, label="Expression strength")
                            cfg_weight = gr.Slider(0.0, 1.0, value=initial_profile.cfg_weight, step=0.05, label="Pacing guidance")
                            min_p = gr.Slider(0.0, 0.5, value=initial_profile.min_p, step=0.01, label="Min P")
                        temperature = gr.Slider(0.05, 1.5, value=initial_profile.temperature, step=0.05, label="Variation")
                        repetition_penalty = gr.Slider(1.0, 2.0, value=initial_profile.repetition_penalty, step=0.05, label="Repetition control")
                        top_p = gr.Slider(0.1, 1.0, value=initial_profile.top_p, step=0.01, label="Top P")
                        with gr.Group(visible=initial_caps.top_k) as top_k_group:
                            top_k = gr.Slider(50, 2000, value=initial_profile.top_k, step=50, label="Top K")

                        gr.Markdown("#### Text cleanup")
                        normalize_unicode = gr.Checkbox(label="Normalize Unicode", value=bool(core.settings["normalize_unicode"]))
                        normalize_punctuation = gr.Checkbox(label="Normalize punctuation", value=bool(core.settings["normalize_punctuation"]))
                        normalize_numbers = gr.Checkbox(label="Read numbers as words", value=bool(core.settings["normalize_numbers"]))
                        replace_urls = gr.Checkbox(label="Replace URLs with the word URL", value=bool(core.settings["replace_urls"]))
                        collapse_punctuation = gr.Checkbox(label="Collapse extreme repeated punctuation", value=bool(core.settings["collapse_repeated_punctuation"]))
                        normalize_whitespace = gr.Checkbox(label="Normalize spaces", value=bool(core.settings["normalize_whitespace"]))
                        preview_btn = gr.Button("Preview processed text")
                        preview_text = gr.Textbox(label="Processed text", lines=6, interactive=False)
                        preview_note = gr.Markdown()

                        gr.Markdown("#### Reliability")
                        quality_check = gr.Checkbox(label="Check generated audio automatically", value=bool(initial_quality_policy["quality_check"]))
                        auto_retries = gr.Slider(0, 3, value=int(initial_quality_policy["auto_retries"]), step=1, label="Automatic retries")
                        best_of_n = gr.Slider(1, 3, value=int(initial_quality_policy["best_of_n"]), step=1, label="Generate candidates and keep the best")
                        verify_stt = gr.Checkbox(label="Verify generated speech with local STT", value=bool(core.settings["verify_stt"]))
                        whisper_model = gr.Dropdown(label="STT verifier size", choices=["tiny", "base", "small"], value=str(core.settings["whisper_model"]))
                        verification_threshold = gr.Slider(0.5, 0.98, value=float(core.settings["verification_threshold"]), step=0.01, label="Transcript similarity threshold")

                    with gr.Accordion("Diagnostics", open=False):
                        diagnostics_box = gr.Code(value=core.format_diagnostics(core.collect_diagnostics(ROOT)), language="json", label="Technical diagnostics", interactive=False)
                        diagnostics_refresh = gr.Button("Refresh diagnostics")
                    with gr.Accordion("About & open-source credits", open=False):
                        gr.Markdown("Creator Studio is an independent local application. The current product name and visual identity are temporary. Current speech models are open-source Chatterbox models; full attribution and licenses are in NOTICE.md, LICENSE, and the repository README.")

    gr.HTML("<div class='footer-note'><span>Runs locally by default.</span><span>Credits & licenses are in About.</span></div>")

    # Settings mirror into simple State objects so actions in earlier tabs can use them
    # without creating duplicate event handlers or depending on tab construction order.
    compute_preference.change(lambda value: value, inputs=compute_preference, outputs=compute_state, queue=False)
    quality_mode.change(lambda value: value, inputs=quality_mode, outputs=quality_state, queue=False)
    offline_mode.change(lambda value: bool(value), inputs=offline_mode, outputs=offline_state, queue=False)

    sound_control_outputs = [
        language_ui, exaggeration, cfg_weight, temperature, repetition_penalty, min_p,
        top_p, top_k, speech_speed, multilingual_expert_group, top_k_group, tag_group, model_state,
    ]
    for component in (model_ui, style):
        component.change(
            update_sound_controls,
            inputs=[model_ui, language_ui, script, compute_state, style],
            outputs=sound_control_outputs,
            queue=False,
        )
    for component in (language_ui, script):
        component.change(core._model_state_line, inputs=[model_ui, language_ui, script, compute_state], outputs=model_state, queue=False)

    for button, token in ((pause_025, "[pause=0.25]"), (pause_05, "[pause=0.5]"), (pause_1, "[pause=1]"), (pause_2, "[pause=2]")):
        button.click(lambda text: text, inputs=script, outputs=script, js=_insert_js(token), queue=False)
    for button, token in tag_buttons:
        button.click(lambda text: text, inputs=script, outputs=script, js=_insert_js(token), queue=False)

    voice_outputs = [voice_dropdown, voice_manage, batch_voice, quick_voice_audio, quick_voice_status]
    quick_voice_save.click(core.add_voice, inputs=[quick_voice_audio, quick_voice_name], outputs=voice_outputs, show_progress="minimal")
    library_voice_save.click(core.add_voice, inputs=[library_voice_audio, library_voice_name], outputs=[voice_dropdown, voice_manage, batch_voice, library_voice_audio, voice_manage_status], show_progress="minimal")
    library_voice_refresh.click(core.refresh_voices, outputs=[voice_dropdown, voice_manage, batch_voice], queue=False)
    voice_manage.change(core.inspect_voice, inputs=voice_manage, outputs=[voice_inspection, voice_preview], queue=False)
    voice_inspect.click(core.inspect_voice, inputs=voice_manage, outputs=[voice_inspection, voice_preview], queue=False)
    rename_voice_btn.click(core.rename_voice, inputs=[voice_manage, rename_voice_name], outputs=[voice_manage, voice_manage_status], queue=False).then(core.sync_voice_selection, inputs=voice_manage, outputs=[voice_dropdown, voice_manage, batch_voice], queue=False)
    duplicate_voice_btn.click(core.duplicate_voice, inputs=voice_manage, outputs=[voice_manage, voice_manage_status], queue=False).then(core.sync_voice_selection, inputs=voice_manage, outputs=[voice_dropdown, voice_manage, batch_voice], queue=False)
    delete_voice_btn.click(core.delete_voice, inputs=voice_manage, outputs=[voice_manage, voice_manage_status], queue=False).then(core.sync_voice_selection, inputs=voice_manage, outputs=[voice_dropdown, voice_manage, batch_voice], queue=False)

    preprocess_inputs = [script, language_ui, raw_mode, normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls, collapse_punctuation, normalize_whitespace]
    preview_btn.click(core.preview_processed_text, inputs=preprocess_inputs, outputs=[preview_text, preview_note], queue=False)

    generation_inputs = [
        script, project_dropdown, voice_dropdown, model_ui, language_ui, style, speech_speed,
        compute_state, quality_state, offline_state, recommended_tuning,
        exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, top_k,
        raw_mode, smart_chunking, max_chars, chunk_gap, seed,
        normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls, collapse_punctuation, normalize_whitespace,
        quality_check, verify_stt, whisper_model, verification_threshold, auto_retries, best_of_n,
        trim_silence, peak_normalize, fade_ms,
    ]
    generation_outputs = [
        output_audio, output_download, metadata_download, create_status, generation_details, history,
        project_takes, model_state, download_confirm, download_confirm_text, pending_model,
        save_result_btn, last_recipe_state,
    ]
    generate_start = generate_btn.click(_generate_busy, outputs=generate_btn, queue=False)
    generate_event = generate_start.then(generate_product, inputs=generation_inputs, outputs=generation_outputs, show_progress="minimal")
    generate_event.then(_generate_idle, outputs=generate_btn, queue=False)
    download_generate_btn.click(download_and_generate_product, inputs=[pending_model, *generation_inputs], outputs=generation_outputs, show_progress="minimal")
    download_cancel_btn.click(cancel_model_prompt, outputs=[download_confirm, download_confirm_text, pending_model], queue=False)

    save_result_btn.click(save_recipe, inputs=[last_recipe_state, recipe_name], outputs=[recipe_status, saved_recipe], queue=False)
    compare_v3_save.click(save_recipe, inputs=[compare_recipe_v3, recipe_name], outputs=[recipe_status, saved_recipe], queue=False)
    compare_turbo_save.click(save_recipe, inputs=[compare_recipe_turbo, recipe_name], outputs=[recipe_status, saved_recipe], queue=False)
    compare_nano_save.click(save_recipe, inputs=[compare_recipe_nano, recipe_name], outputs=[recipe_status, saved_recipe], queue=False)
    apply_recipe_btn.click(
        apply_recipe, inputs=saved_recipe,
        outputs=[voice_dropdown, model_ui, language_ui, style, speech_speed, exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, top_k, saved_recipe_status],
        queue=False,
    ).then(update_sound_controls, inputs=[model_ui, language_ui, script, compute_state, style], outputs=sound_control_outputs, queue=False)
    delete_recipe_btn.click(delete_recipe, inputs=saved_recipe, outputs=[saved_recipe, saved_recipe_status], queue=False)

    for component in (language_ui, script):
        component.change(refresh_compare_picker, inputs=[language_ui, script, compare_picker], outputs=[compare_picker, compare_status, compare_btn], queue=False)
    compare_picker.change(compare_selection_note, inputs=[compare_picker, language_ui, script], outputs=[compare_status, compare_btn], queue=False)

    compare_inputs = [
        compare_picker, script, project_dropdown, voice_dropdown, language_ui, style, speech_speed,
        compute_state, quality_state, offline_state, recommended_tuning,
        exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, top_k,
        raw_mode, smart_chunking, max_chars, chunk_gap, seed,
        normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls, collapse_punctuation, normalize_whitespace,
        quality_check, verify_stt, whisper_model, verification_threshold, auto_retries, best_of_n,
        trim_silence, peak_normalize, fade_ms,
    ]
    compare_outputs = [
        compare_v3_audio, compare_v3_download, compare_v3_save, compare_recipe_v3,
        compare_turbo_audio, compare_turbo_download, compare_turbo_save, compare_recipe_turbo,
        compare_nano_audio, compare_nano_download, compare_nano_save, compare_recipe_nano,
        compare_status,
    ]
    compare_start = compare_btn.click(_compare_busy, outputs=[compare_btn, compare_cancel], queue=False)
    compare_event = compare_start.then(compare_selected_models, inputs=compare_inputs, outputs=compare_outputs, show_progress="minimal")
    compare_event.then(_compare_idle, inputs=compare_picker, outputs=[compare_btn, compare_cancel], queue=False)
    compare_cancel.click(lambda: "Stopping comparison…", outputs=compare_status, cancels=[compare_event], queue=False).then(_compare_idle, inputs=compare_picker, outputs=[compare_btn, compare_cancel], queue=False)

    project_refresh.click(core.refresh_projects, outputs=project_dropdown, queue=False)
    create_project_btn.click(core.create_project, inputs=project_name, outputs=[project_dropdown, project_status], queue=False)
    load_project_btn.click(core.load_project, inputs=project_dropdown, outputs=[script, voice_dropdown, model_ui, language_ui, project_name, project_status], queue=False)
    save_project_btn.click(core.save_project, inputs=[project_dropdown, project_name, script, voice_dropdown, model_ui, language_ui, compute_state], outputs=project_status, queue=False)
    delete_project_btn.click(core.delete_project, inputs=project_dropdown, outputs=[project_dropdown, project_status], queue=False)
    takes_refresh.click(core.project_take_choices, inputs=project_dropdown, outputs=project_takes, queue=False)
    take_load.click(core.load_take, inputs=project_takes, outputs=[output_audio, output_download], queue=False)
    if initial_project:
        demo.load(core.project_take_choices, inputs=project_dropdown, outputs=project_takes, queue=False)
    for component in (script, voice_dropdown, model_ui, language_ui):
        component.change(core.autosave_project, inputs=[project_dropdown, script, voice_dropdown, model_ui, language_ui, compute_state], outputs=project_status, queue=False)

    history_refresh.click(core.refresh_history, outputs=history, queue=False)
    history_load.click(core.load_history, inputs=history, outputs=[history_audio, history_download, history_metadata, history_note], queue=False)
    history_delete.click(core.delete_history, inputs=history, outputs=[history, history_note], queue=False)

    batch_model.change(update_batch_language, inputs=[batch_model, batch_language], outputs=batch_language, queue=False)
    batch_parse.click(core.preview_batch_file, inputs=batch_file, outputs=[batch_state, batch_table, batch_status], queue=False)
    batch_inputs = [
        batch_state, batch_voice, batch_model, batch_language, speech_speed, compute_state, offline_state, no_auto_download,
        exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, top_k,
        raw_mode, smart_chunking, max_chars, chunk_gap, seed,
        normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls, collapse_punctuation, normalize_whitespace,
        quality_check, verify_stt, whisper_model, verification_threshold, auto_retries, best_of_n,
        batch_fit, batch_stretch,
    ]
    batch_run.click(core.run_batch_ui, inputs=batch_inputs, outputs=[batch_manifest, batch_output_dir, batch_status], show_progress="minimal")

    stt_btn.click(core.transcribe_ui, inputs=[stt_audio, stt_quality, stt_language, compute_state], outputs=[stt_text, stt_segments, stt_status], show_progress="minimal")
    stt_install_btn.click(install_speech_tools_inline, outputs=[stt_status, stt_install_btn], show_progress="minimal")

    all_model_outputs = model_card_components
    for model_id in MODEL_IDS:
        install_btn, use_btn, update_btn, remove_btn = model_buttons[model_id]
        install_btn.click(_model_install_action(model_id), inputs=offline_state, outputs=model_action_status, show_progress="minimal").then(refresh_all_model_cards, outputs=all_model_outputs, queue=False).then(refresh_compare_picker, inputs=[language_ui, script, compare_picker], outputs=[compare_picker, compare_status, compare_btn], queue=False)
        use_btn.click(_model_use_action(model_id), inputs=[compute_state, offline_state], outputs=model_action_status, show_progress="minimal").then(refresh_all_model_cards, outputs=all_model_outputs, queue=False)
        update_btn.click(_model_update_action(model_id), inputs=offline_state, outputs=model_action_status, show_progress="minimal").then(refresh_all_model_cards, outputs=all_model_outputs, queue=False)
        remove_btn.click(_model_remove_action(model_id), outputs=model_action_status, queue=False).then(refresh_all_model_cards, outputs=all_model_outputs, queue=False).then(refresh_compare_picker, inputs=[language_ui, script, compare_picker], outputs=[compare_picker, compare_status, compare_btn], queue=False)

    for component in (model_search, model_filter):
        component.change(filter_model_cards, inputs=[model_search, model_filter], outputs=model_groups, queue=False)
    demo.load(refresh_all_model_cards, outputs=all_model_outputs, queue=False)

    save_preferences.click(
        lambda compute, quality, offline, trim, normalize, fade: core.save_user_preferences(compute, quality, offline, False, trim, normalize, fade),
        inputs=[compute_preference, quality_mode, offline_mode, trim_silence, peak_normalize, fade_ms],
        outputs=preferences_status, queue=False,
    )
    install_speech_btn.click(core.install_speech_tools, outputs=speech_tools_note, show_progress="minimal")
    diagnostics_refresh.click(core.refresh_hardware, outputs=[hardware_box, diagnostics_box], queue=False)
    quality_mode.change(core.apply_quality_mode, inputs=quality_mode, outputs=[quality_check, auto_retries, best_of_n], queue=False)


def launch():
    host = os.getenv("CHATTERBOX_STUDIO_HOST", "127.0.0.1")
    port = int(os.getenv("CHATTERBOX_STUDIO_PORT", "7860"))
    share = os.getenv("CHATTERBOX_STUDIO_SHARE", "0").strip().lower() in {"1", "true", "yes"}
    css_parts = []
    if core.CSS_PATH.exists():
        css_parts.append(core.CSS_PATH.read_text(encoding="utf-8"))
    if UI_CSS_PATH.exists():
        css_parts.append(UI_CSS_PATH.read_text(encoding="utf-8"))
    demo.queue(default_concurrency_limit=1).launch(
        server_name=host,
        server_port=port,
        share=share,
        inbrowser=False,
        css="\n".join(css_parts),
        show_error=True,
    )


if __name__ == "__main__":
    launch()
