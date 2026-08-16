from __future__ import annotations

import html
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import gradio as gr

from studio.audio import AudioProcessOptions, process_audio
from studio.batch import BatchItem, load_batch, preview_rows
from studio.batch_runner import run_batch
from studio.diagnostics import collect_diagnostics, format_diagnostics
from studio.engine import ChatterboxEngine
from studio.hardware import collect_hardware_profile, hardware_summary, resolve_compute_preference
from studio.model_manager import LocalModelManager, set_hf_offline
from studio.models import (
    DEFAULT_MODEL_ID,
    MODEL_SPECS,
    MULTILINGUAL_LANGUAGES,
    PARALINGUISTIC_TAGS,
    language_code_from_name,
    language_name_from_code,
)
from studio.preprocess import PreprocessOptions, process_text
from studio.presets import PRESETS, preset_values
from studio.product import (
    AUTO_MODEL,
    COMPUTE_CHOICES,
    MODEL_SELECTIONS,
    MODEL_UI_DESCRIPTIONS,
    MODEL_UI_NAMES,
    QUALITY_MODES,
    ProductSystemProfile,
    compatible_models,
    human_model_status,
    model_detail,
    model_id_from_ui_name,
    model_ui_name,
    quality_policy,
    resolve_language,
    resolve_model_id,
    safe_compare_order,
)
from studio.projects import ProjectStore
from studio.reliability import GenerationPolicy, generate_reliably
from studio.settings import SettingsStore
from studio.transcription import transcribe_audio
from studio.voices import VoiceLibrary

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
VOICE_DIR = DATA_DIR / "voices"
PROJECT_DIR = DATA_DIR / "projects"
OUTPUT_DIR = ROOT / "outputs"
BATCH_DIR = OUTPUT_DIR / "batches"
CSS_PATH = ROOT / "assets" / "style.css"

for directory in (VOICE_DIR, PROJECT_DIR, OUTPUT_DIR, BATCH_DIR):
    directory.mkdir(parents=True, exist_ok=True)

voices = VoiceLibrary(VOICE_DIR)
projects = ProjectStore(PROJECT_DIR)
settings_store = SettingsStore(DATA_DIR / "settings.json")
settings = settings_store.load()
model_manager = LocalModelManager(DATA_DIR / "model_state.json")
hardware = collect_hardware_profile()
engine = ChatterboxEngine(OUTPUT_DIR)
set_hf_offline(bool(settings.get("offline_mode", False)))

LANGUAGE_CHOICES = ["Auto", *MULTILINGUAL_LANGUAGES.values()]
SAMPLE_SCRIPT = "Type what you want to hear. [pause=0.5] Exact pauses are available when you need them."


def _save_settings(values: dict) -> None:
    current = settings_store.load()
    current.update(values)
    settings_store.save(current)


def _friendly_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    replacements = {
        "Add and select a saved voice profile first.": "Choose a voice first, or add one here.",
        "The selected voice profile could not be found.": "That saved voice is no longer available. Choose another voice.",
    }
    return replacements.get(message, message)


def _product_system_profile(compute_preference: str | None) -> tuple[ProductSystemProfile, str, str]:
    device, device_label = resolve_compute_preference(compute_preference, hardware)
    return (
        ProductSystemProfile(
            compute="gpu" if device in {"cuda", "mps"} else "cpu",
            ram_gb=hardware.ram_gb,
            vram_gb=hardware.vram_gb,
        ),
        device,
        device_label,
    )


def _resolve_choice(model_ui: str | None, language_ui: str | None, script: str, compute_preference: str | None) -> tuple[str, str, str, str]:
    profile, device, device_label = _product_system_profile(compute_preference)
    language_name = resolve_language(language_ui, script)
    model_id = resolve_model_id(model_ui, language_ui, script, profile)
    return model_id, language_name, device, device_label


def _gr_progress(progress: gr.Progress, desc: str, current: int | None, total: int | None, unit: str = "step") -> None:
    if current is None:
        progress((0, None), desc=desc, unit=unit)
    elif total:
        progress((int(current), int(total)), desc=desc, unit=unit)
    else:
        progress((int(current), None), desc=desc, unit=unit)


def _model_download_progress(progress: gr.Progress):
    def callback(current: int, total: int | None, desc: str) -> None:
        _gr_progress(progress, desc or "Downloading model…", current, total, unit="file")

    return callback


def _engine_progress(progress: gr.Progress, prefix: str = ""):
    def callback(desc: str, current: int | None, total: int | None) -> None:
        label = f"{prefix}{desc}" if prefix else desc
        _gr_progress(progress, label, current, total, unit="chunk")

    return callback


def _configure_engine(
    model_id: str,
    compute_preference: str,
    offline_mode: bool,
    auto_download_models: bool,
    progress: gr.Progress,
) -> str:
    _, device, device_label = _product_system_profile(compute_preference)
    engine.set_device(device, device_label)
    set_hf_offline(bool(offline_mode))
    status = model_manager.ensure(
        model_id,
        auto_download=bool(auto_download_models),
        offline=bool(offline_mode),
        progress=_model_download_progress(progress),
    )
    if not status.snapshot_path:
        raise RuntimeError("The selected model is not ready yet.")
    engine.set_model_path(model_id, status.snapshot_path)
    return device_label


def _preprocess_options(
    raw_mode,
    normalize_unicode,
    normalize_punctuation,
    normalize_numbers,
    replace_urls,
    collapse_punctuation,
    normalize_whitespace,
) -> PreprocessOptions:
    if raw_mode:
        return PreprocessOptions(
            normalize_unicode=False,
            normalize_punctuation=False,
            normalize_numbers=False,
            replace_urls=False,
            collapse_repeated_punctuation=False,
            normalize_whitespace=False,
        )
    return PreprocessOptions(
        normalize_unicode=bool(normalize_unicode),
        normalize_punctuation=bool(normalize_punctuation),
        normalize_numbers=bool(normalize_numbers),
        replace_urls=bool(replace_urls),
        collapse_repeated_punctuation=bool(collapse_punctuation),
        normalize_whitespace=bool(normalize_whitespace),
    )


def _policy(
    raw_mode,
    normalize_unicode,
    normalize_punctuation,
    normalize_numbers,
    replace_urls,
    collapse_punctuation,
    normalize_whitespace,
    quality_check,
    verify_stt,
    whisper_model,
    verification_threshold,
    auto_retries,
    best_of_n,
) -> GenerationPolicy:
    return GenerationPolicy(
        preprocess=_preprocess_options(
            raw_mode,
            normalize_unicode,
            normalize_punctuation,
            normalize_numbers,
            replace_urls,
            collapse_punctuation,
            normalize_whitespace,
        ),
        quality_check=bool(quality_check),
        verify_stt=bool(verify_stt),
        whisper_model=str(whisper_model or "tiny"),
        verification_threshold=float(verification_threshold),
        auto_retries=int(auto_retries),
        best_of_n=int(best_of_n),
    )


def _generation_kwargs(
    voice_name,
    model_id,
    language_name,
    exaggeration,
    cfg_weight,
    temperature,
    repetition_penalty,
    min_p,
    top_p,
    top_k,
    speech_speed,
    raw_mode,
    smart_chunking,
    max_chars,
    chunk_gap_seconds,
    seed,
    progress_callback=None,
) -> dict:
    voice_path = voices.path_for(voice_name)
    if voice_path is None:
        raise ValueError("Choose a voice first, or add one here.")
    language_id = language_code_from_name(language_name)
    if model_id in {"turbo", "nano"}:
        language_id = "en"
    return {
        "voice_path": voice_path,
        "model_id": model_id,
        "language_id": language_id,
        "exaggeration": float(exaggeration),
        "cfg_weight": float(cfg_weight),
        "temperature": float(temperature),
        "repetition_penalty": float(repetition_penalty),
        "min_p": float(min_p),
        "top_p": float(top_p),
        "top_k": int(top_k),
        "speech_speed": float(speech_speed),
        "raw_mode": bool(raw_mode),
        "smart_chunking": bool(smart_chunking),
        "max_chars": int(max_chars),
        "chunk_gap_seconds": float(chunk_gap_seconds),
        "seed": int(seed),
        "progress_callback": progress_callback,
    }


def _finish_audio(path: Path, trim_silence: bool, peak_normalize: bool, fade_ms: int) -> Path:
    if not (trim_silence or peak_normalize or int(fade_ms) > 0):
        return path
    processed = path.with_name(f"{path.stem}_final.wav")
    return process_audio(
        path,
        processed,
        AudioProcessOptions(
            trim_silence=bool(trim_silence),
            peak_normalize=bool(peak_normalize),
            fade_ms=int(fade_ms),
        ),
    )


def _project_choices() -> list[str]:
    return [item.id for item in projects.list()]


def _voice_choices() -> list[str]:
    return voices.list()


def _model_state_line(model_ui: str, language_ui: str, script: str, compute_preference: str) -> str:
    try:
        model_id, language_name, _, device_label = _resolve_choice(model_ui, language_ui, script, compute_preference)
        status = model_manager.status(model_id)
        ready = "Ready" if status.installed else "Download needed"
        return f"**Auto choice:** {model_ui_name(model_id)} · {language_name} · {device_label} · {ready}" if model_ui == AUTO_MODEL else f"**{model_ui_name(model_id)}** · {language_name} · {device_label} · {ready}"
    except Exception as exc:
        return f"⚠️ {_friendly_error(exc)}"


def apply_preset(name: str):
    return preset_values(name)


def apply_quality_mode(name: str):
    values = quality_policy(name)
    return bool(values["quality_check"]), int(values["auto_retries"]), int(values["best_of_n"])


def preview_processed_text(
    script,
    language_ui,
    raw_mode,
    normalize_unicode,
    normalize_punctuation,
    normalize_numbers,
    replace_urls,
    collapse_punctuation,
    normalize_whitespace,
):
    language_name = resolve_language(language_ui, script or "")
    result = process_text(
        script or "",
        language_id=language_code_from_name(language_name),
        options=_preprocess_options(
            raw_mode,
            normalize_unicode,
            normalize_punctuation,
            normalize_numbers,
            replace_urls,
            collapse_punctuation,
            normalize_whitespace,
        ),
    )
    if raw_mode:
        note = "Raw mode leaves the text unchanged."
    elif result.warnings:
        note = "⚠️ " + " ".join(result.warnings)
    else:
        note = "No text changes." if not result.changed else "This is the exact text the generation pipeline will receive."
    return result.processed, note


def add_voice(audio_path: str | None, name: str | None):
    try:
        saved_name, _ = voices.save(audio_path, name)
        profile = voices.profile(saved_name)
    except Exception as exc:
        return gr.update(), gr.update(), gr.update(), gr.update(), f"❌ {_friendly_error(exc)}"
    choices = _voice_choices()
    warning = f" · {profile.warning}" if profile and profile.warning else ""
    duration = f"{profile.duration_seconds:.1f}s" if profile and profile.duration_seconds is not None else "saved"
    return (
        gr.update(choices=choices, value=saved_name),
        gr.update(choices=choices, value=saved_name),
        gr.update(choices=choices, value=saved_name),
        gr.update(value=None),
        f"✅ **{saved_name}** is ready · {duration}{warning}",
    )


def refresh_voices():
    choices = _voice_choices()
    value = choices[0] if choices else None
    return gr.update(choices=choices, value=value), gr.update(choices=choices, value=value), gr.update(choices=choices, value=value)


def sync_voice_selection(name: str | None):
    choices = _voice_choices()
    value = name if name in choices else (choices[0] if choices else None)
    return gr.update(choices=choices, value=value), gr.update(choices=choices, value=value), gr.update(choices=choices, value=value)


def inspect_voice(name: str | None):
    profile = voices.profile(name)
    if profile is None:
        return "Choose a saved voice.", None
    bits = []
    if profile.duration_seconds is not None:
        bits.append(f"{profile.duration_seconds:.1f}s")
    if profile.sample_rate:
        bits.append(f"{profile.sample_rate} Hz")
    if profile.silence_ratio is not None:
        bits.append(f"silence {profile.silence_ratio:.0%}")
    if profile.clipping_ratio is not None:
        bits.append(f"clipping {profile.clipping_ratio:.2%}")
    note = f"**{profile.name}** · " + " · ".join(bits)
    if profile.warning:
        note += f"  \n⚠️ {profile.warning}"
    return note, profile.path


def rename_voice(name: str | None, new_name: str | None):
    if not name:
        return gr.update(), "Choose a saved voice first."
    try:
        renamed = voices.rename(name, new_name or name)
    except Exception as exc:
        return gr.update(), f"❌ {_friendly_error(exc)}"
    choices = _voice_choices()
    return gr.update(choices=choices, value=renamed), f"✅ Renamed to **{renamed}**."


def duplicate_voice(name: str | None):
    if not name:
        return gr.update(), "Choose a saved voice first."
    try:
        duplicated = voices.duplicate(name)
    except Exception as exc:
        return gr.update(), f"❌ {_friendly_error(exc)}"
    choices = _voice_choices()
    return gr.update(choices=choices, value=duplicated), f"✅ Duplicated as **{duplicated}**."


def delete_voice(name: str | None):
    if not name:
        return gr.update(), "Choose a saved voice first."
    if voices.delete(name):
        choices = _voice_choices()
        return gr.update(choices=choices, value=choices[0] if choices else None), f"Deleted **{name}**."
    return gr.update(), "Voice not found."


def create_project(name: str):
    try:
        project = projects.create(name or "Untitled Project")
    except Exception as exc:
        return gr.update(), f"❌ {_friendly_error(exc)}"
    choices = _project_choices()
    return gr.update(choices=choices, value=project["id"]), f"✅ Created **{project['name']}**."


def refresh_projects():
    choices = _project_choices()
    return gr.update(choices=choices, value=choices[0] if choices else None)


def load_project(project_id: str | None):
    project = projects.load(project_id)
    if project is None:
        return "", gr.update(), gr.update(), gr.update(), "", "No project selected."
    saved = project.get("settings") or {}
    model_selection = saved.get("model_selection")
    if model_selection not in MODEL_SELECTIONS:
        model_id = project.get("model_id") if project.get("model_id") in MODEL_SPECS else DEFAULT_MODEL_ID
        model_selection = model_ui_name(model_id)
    language_selection = saved.get("language_selection")
    if language_selection not in LANGUAGE_CHOICES:
        language_selection = language_name_from_code(project.get("language", "en"))
    voice = project.get("voice") if project.get("voice") in _voice_choices() else None
    return (
        project.get("script", ""),
        gr.update(choices=_voice_choices(), value=voice),
        gr.update(value=model_selection),
        gr.update(value=language_selection),
        project.get("name", project_id or ""),
        f"✅ Loaded **{project.get('name', project_id)}**.",
    )


def save_project(project_id, project_name, script, voice_name, model_ui, language_ui, compute_preference):
    if not project_id:
        return "Create or load a project first."
    try:
        try:
            actual_model, language_name, _, _ = _resolve_choice(model_ui, language_ui, script or "", compute_preference)
        except Exception:
            actual_model = model_id_from_ui_name(model_ui) or DEFAULT_MODEL_ID
            language_name = resolve_language(language_ui, script or "")
        snapshot = settings_store.load()
        snapshot.update({"model_selection": model_ui, "language_selection": language_ui})
        project = projects.save(
            project_id,
            {
                "name": project_name or project_id,
                "script": script or "",
                "voice": voice_name,
                "model_id": actual_model,
                "language": language_code_from_name(language_name),
                "settings": snapshot,
            },
        )
        return f"✅ Saved **{project['name']}**."
    except Exception as exc:
        return f"❌ {_friendly_error(exc)}"


def autosave_project(project_id, script, voice_name, model_ui, language_ui, compute_preference):
    if not project_id or projects.load(project_id) is None:
        return ""
    project = projects.load(project_id) or {}
    return save_project(project_id, project.get("name", project_id), script, voice_name, model_ui, language_ui, compute_preference)


def delete_project(project_id: str | None):
    if not project_id:
        return gr.update(), "No project selected."
    if projects.delete(project_id):
        choices = _project_choices()
        return gr.update(choices=choices, value=choices[0] if choices else None), "Project deleted."
    return gr.update(), "Project not found."


def project_take_choices(project_id: str | None):
    if not project_id:
        return gr.update(choices=[], value=None)
    takes = [str(path) for path in projects.list_takes(project_id)]
    return gr.update(choices=takes, value=takes[0] if takes else None)


def load_take(take_path: str | None):
    if not take_path or not Path(take_path).exists():
        return None, None
    return take_path, take_path


def generate_audio(
    script,
    project_id,
    voice_name,
    model_ui,
    language_ui,
    preset_name,
    speech_speed,
    compute_preference,
    quality_mode,
    offline_mode,
    auto_download_models,
    exaggeration,
    cfg_weight,
    temperature,
    repetition_penalty,
    min_p,
    top_p,
    top_k,
    raw_mode,
    smart_chunking,
    max_chars,
    chunk_gap_seconds,
    seed,
    normalize_unicode,
    normalize_punctuation,
    normalize_numbers,
    replace_urls,
    collapse_punctuation,
    normalize_whitespace,
    quality_check,
    verify_stt,
    whisper_model,
    verification_threshold,
    auto_retries,
    best_of_n,
    trim_silence,
    peak_normalize,
    fade_ms,
    progress=gr.Progress(),
):
    if not (script or "").strip():
        raise gr.Error("Write something first.")
    try:
        model_id, language_name, _, _ = _resolve_choice(model_ui, language_ui, script, compute_preference)
        device_label = _configure_engine(model_id, compute_preference, offline_mode, auto_download_models, progress)
        kwargs = _generation_kwargs(
            voice_name,
            model_id,
            language_name,
            exaggeration,
            cfg_weight,
            temperature,
            repetition_penalty,
            min_p,
            top_p,
            top_k,
            speech_speed,
            raw_mode,
            smart_chunking,
            max_chars,
            chunk_gap_seconds,
            seed,
            progress_callback=_engine_progress(progress),
        )
        reliable = generate_reliably(
            engine,
            script,
            policy=_policy(
                raw_mode,
                normalize_unicode,
                normalize_punctuation,
                normalize_numbers,
                replace_urls,
                collapse_punctuation,
                normalize_whitespace,
                quality_check,
                verify_stt,
                whisper_model,
                verification_threshold,
                auto_retries,
                best_of_n,
            ),
            **kwargs,
        )
        selected = reliable.selected
        final_audio = _finish_audio(selected.result.audio_path, trim_silence, peak_normalize, fade_ms)
        project_take_update = gr.update()
        if project_id:
            try:
                projects.add_take(project_id, final_audio, selected.result.metadata_path)
                project_take_update = project_take_choices(project_id)
            except Exception:
                pass

        _save_settings(
            {
                "voice": voice_name,
                "project": project_id,
                "model_id": model_id,
                "model_selection": model_ui,
                "language": language_code_from_name(language_name),
                "language_selection": language_ui,
                "preset": preset_name,
                "generation_quality": quality_mode,
                "compute_preference": compute_preference,
                "offline_mode": bool(offline_mode),
                "auto_download_models": bool(auto_download_models),
                "exaggeration": float(exaggeration),
                "cfg_weight": float(cfg_weight),
                "temperature": float(temperature),
                "repetition_penalty": float(repetition_penalty),
                "min_p": float(min_p),
                "top_p": float(top_p),
                "top_k": int(top_k),
                "speech_speed": float(speech_speed),
                "raw_mode": bool(raw_mode),
                "smart_chunking": bool(smart_chunking),
                "max_chars": int(max_chars),
                "chunk_gap_seconds": float(chunk_gap_seconds),
                "seed": int(seed),
                "normalize_unicode": bool(normalize_unicode),
                "normalize_punctuation": bool(normalize_punctuation),
                "normalize_numbers": bool(normalize_numbers),
                "replace_urls": bool(replace_urls),
                "collapse_repeated_punctuation": bool(collapse_punctuation),
                "normalize_whitespace": bool(normalize_whitespace),
                "quality_check": bool(quality_check),
                "verify_stt": bool(verify_stt),
                "whisper_model": whisper_model,
                "verification_threshold": float(verification_threshold),
                "auto_retries": int(auto_retries),
                "best_of_n": int(best_of_n),
                "trim_silence": bool(trim_silence),
                "peak_normalize": bool(peak_normalize),
                "fade_ms": int(fade_ms),
            }
        )
        progress(None)
        warnings = []
        if selected.quality and selected.quality.warnings:
            warnings.extend(selected.quality.warnings)
        if selected.verification and selected.verification.warning:
            warnings.append(selected.verification.warning)
        short_status = f"✅ Ready · **{model_ui_name(model_id)}** · {device_label}"
        if warnings:
            short_status += "  \n⚠️ " + " ".join(dict.fromkeys(warnings))
        details = (
            f"Model: **{model_ui_name(model_id)}** · language: **{language_name}** · quality: **{quality_mode}**  \n"
            f"Seed `{selected.result.seed}` · {selected.result.chunk_count} chunk(s) · {len(reliable.candidates)} candidate(s) · score `{selected.score:.3f}`"
        )
        return (
            str(final_audio),
            str(final_audio),
            str(selected.result.metadata_path),
            short_status,
            details,
            gr.update(choices=engine.recent_outputs(), value=final_audio.name),
            project_take_update,
            _model_state_line(model_ui, language_ui, script, compute_preference),
        )
    except Exception as exc:
        progress(None)
        raise gr.Error(_friendly_error(exc)) from exc


def compare_models(
    script,
    project_id,
    voice_name,
    language_ui,
    preset_name,
    speech_speed,
    compute_preference,
    quality_mode,
    offline_mode,
    auto_download_models,
    exaggeration,
    cfg_weight,
    temperature,
    repetition_penalty,
    min_p,
    top_p,
    top_k,
    raw_mode,
    smart_chunking,
    max_chars,
    chunk_gap_seconds,
    seed,
    normalize_unicode,
    normalize_punctuation,
    normalize_numbers,
    replace_urls,
    collapse_punctuation,
    normalize_whitespace,
    quality_check,
    verify_stt,
    whisper_model,
    verification_threshold,
    auto_retries,
    best_of_n,
    trim_silence,
    peak_normalize,
    fade_ms,
    progress=gr.Progress(),
):
    if not (script or "").strip():
        raise gr.Error("Write something first.")
    language_name = resolve_language(language_ui, script)
    model_ids = safe_compare_order(compatible_models(language_ui, script))
    results: dict[str, str | None] = {model_id: None for model_id in MODEL_SPECS}
    notes: list[str] = []
    total_models = len(model_ids)
    try:
        for index, model_id in enumerate(model_ids, 1):
            label = model_ui_name(model_id)
            _gr_progress(progress, f"Comparing {index}/{total_models} · preparing {label}…", index - 1, total_models, unit="model")
            try:
                _configure_engine(model_id, compute_preference, offline_mode, auto_download_models, progress)
                kwargs = _generation_kwargs(
                    voice_name,
                    model_id,
                    language_name,
                    exaggeration,
                    cfg_weight,
                    temperature,
                    repetition_penalty,
                    min_p,
                    top_p,
                    top_k,
                    speech_speed,
                    raw_mode,
                    smart_chunking,
                    max_chars,
                    chunk_gap_seconds,
                    seed,
                    progress_callback=lambda desc, current, total, _index=index, _label=label: _gr_progress(
                        progress,
                        f"{_index}/{total_models} · {_label} · {desc}" + (f" {current}/{total}" if current is not None and total else ""),
                        _index - 1,
                        total_models,
                        unit="model",
                    ),
                )
                reliable = generate_reliably(
                    engine,
                    script,
                    policy=_policy(
                        raw_mode,
                        normalize_unicode,
                        normalize_punctuation,
                        normalize_numbers,
                        replace_urls,
                        collapse_punctuation,
                        normalize_whitespace,
                        quality_check,
                        verify_stt,
                        whisper_model,
                        verification_threshold,
                        auto_retries,
                        best_of_n,
                    ),
                    **kwargs,
                )
                final_audio = _finish_audio(reliable.selected.result.audio_path, trim_silence, peak_normalize, fade_ms)
                results[model_id] = str(final_audio)
                notes.append(f"{label} ✓")
                if project_id:
                    try:
                        projects.add_take(
                            project_id,
                            final_audio,
                            reliable.selected.result.metadata_path,
                            segment_id=f"compare-{model_id}",
                        )
                    except Exception:
                        pass
            except Exception as exc:
                notes.append(f"{label}: {_friendly_error(exc)}")
            finally:
                engine.unload()
            _gr_progress(progress, f"Compared {index}/{total_models}", index, total_models, unit="model")
        progress(None)
        multilingual = results["multilingual-v3"]
        turbo = results["turbo"]
        nano = results["nano"]
        return (
            gr.update(value=multilingual, visible=multilingual is not None),
            gr.update(value=multilingual, visible=multilingual is not None),
            gr.update(value=turbo, visible=turbo is not None),
            gr.update(value=turbo, visible=turbo is not None),
            gr.update(value=nano, visible=nano is not None),
            gr.update(value=nano, visible=nano is not None),
            " · ".join(notes),
        )
    except Exception as exc:
        progress(None)
        engine.unload()
        raise gr.Error(_friendly_error(exc)) from exc


def refresh_history():
    recent = engine.recent_outputs()
    return gr.update(choices=recent, value=recent[0] if recent else None)


def load_history(filename: str | None):
    path = engine.output_path(filename)
    metadata = engine.metadata_path(filename)
    if path is None:
        return None, None, None, "Generation not found."
    note = f"Loaded **{path.name}**."
    if metadata:
        try:
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            model_id = payload.get("model", {}).get("id", "")
            if model_id in MODEL_SPECS:
                note += f"  \n{model_ui_name(model_id)} · seed `{payload.get('seed', '?')}`"
        except Exception:
            pass
    return str(path), str(path), str(metadata) if metadata else None, note


def delete_history(filename: str | None):
    path = engine.output_path(filename)
    if path is None:
        return gr.update(), "Generation not found."
    metadata = path.with_suffix(".json")
    try:
        path.unlink()
        if metadata.exists():
            metadata.unlink()
    except OSError as exc:
        return gr.update(), f"❌ {_friendly_error(exc)}"
    recent = engine.recent_outputs()
    return gr.update(choices=recent, value=recent[0] if recent else None), "Deleted."


def preview_batch_file(file_path: str | None):
    if not file_path:
        return [], [], "Choose a TXT, MD, CSV, JSON, SRT or VTT file."
    try:
        items = load_batch(file_path)
    except Exception as exc:
        return [], [], f"❌ {_friendly_error(exc)}"
    state = [
        {"id": item.id, "text": item.text, "start_seconds": item.start_seconds, "end_seconds": item.end_seconds}
        for item in items
    ]
    timed = sum(1 for item in items if item.target_duration_seconds is not None)
    return state, preview_rows(items), f"✅ {len(items)} item(s) ready · {timed} timed subtitle cue(s)."


def run_batch_ui(
    state,
    voice_name,
    model_ui,
    language_ui,
    speech_speed,
    compute_preference,
    offline_mode,
    auto_download_models,
    exaggeration,
    cfg_weight,
    temperature,
    repetition_penalty,
    min_p,
    top_p,
    top_k,
    raw_mode,
    smart_chunking,
    max_chars,
    chunk_gap_seconds,
    seed,
    normalize_unicode,
    normalize_punctuation,
    normalize_numbers,
    replace_urls,
    collapse_punctuation,
    normalize_whitespace,
    quality_check,
    verify_stt,
    whisper_model,
    verification_threshold,
    auto_retries,
    best_of_n,
    fit_timing,
    max_stretch,
    progress=gr.Progress(),
):
    if not state:
        raise gr.Error("Add a batch file first.")
    items = [
        BatchItem(
            id=str(item.get("id")),
            text=str(item.get("text") or ""),
            start_seconds=item.get("start_seconds"),
            end_seconds=item.get("end_seconds"),
        )
        for item in state
    ]
    representative = " ".join(item.text for item in items[:8])
    try:
        model_id, language_name, _, _ = _resolve_choice(model_ui, language_ui, representative, compute_preference)
        _configure_engine(model_id, compute_preference, offline_mode, auto_download_models, progress)
        kwargs = _generation_kwargs(
            voice_name,
            model_id,
            language_name,
            exaggeration,
            cfg_weight,
            temperature,
            repetition_penalty,
            min_p,
            top_p,
            top_k,
            speech_speed,
            raw_mode,
            smart_chunking,
            max_chars,
            chunk_gap_seconds,
            seed,
            progress_callback=None,
        )
        kwargs.pop("progress_callback", None)
        summary = run_batch(
            engine,
            items,
            BATCH_DIR,
            generation_kwargs=kwargs,
            policy=_policy(
                raw_mode,
                normalize_unicode,
                normalize_punctuation,
                normalize_numbers,
                replace_urls,
                collapse_punctuation,
                normalize_whitespace,
                quality_check,
                verify_stt,
                whisper_model,
                verification_threshold,
                auto_retries,
                best_of_n,
            ),
            fit_to_timing=bool(fit_timing),
            max_duration_stretch=float(max_stretch),
            progress_callback=lambda desc, current, total: _gr_progress(progress, desc, current, total, unit="item"),
        )
        progress(None)
        return (
            str(summary.manifest_path),
            str(summary.output_dir),
            f"✅ Generated **{summary.generated}** · failed **{summary.failed}** · {model_ui_name(model_id)}.",
        )
    except Exception as exc:
        progress(None)
        raise gr.Error(_friendly_error(exc)) from exc


def transcribe_ui(audio_path, quality_mode, language_ui, compute_preference, progress=gr.Progress()):
    if not audio_path:
        raise gr.Error("Choose or record audio first.")
    try:
        progress((0, None), desc="Listening…")
        language_id = None if language_ui == "Auto" else language_code_from_name(language_ui)
        result = transcribe_audio(
            audio_path,
            mode=quality_mode,
            language_id=language_id,
            compute_preference=(compute_preference or "Auto").lower(),
        )
        progress(None)
        probability = f" · {result.language_probability:.0%} confidence" if result.language_probability is not None else ""
        detected = result.language or "auto"
        rows = [[segment["start"], segment["end"], segment["text"]] for segment in result.segments]
        return result.text, rows, f"✅ Language: **{detected}**{probability}"
    except Exception as exc:
        progress(None)
        raise gr.Error(_friendly_error(exc)) from exc


def _model_cards_html(checked_model_id: str | None = None, checked_status=None) -> str:
    cards = []
    for model_id in MODEL_SPECS:
        status = checked_status if checked_model_id == model_id and checked_status is not None else model_manager.status(model_id)
        loaded = engine.loaded_model_id == model_id
        state = human_model_status(status.installed, loaded, status.update_available)
        size = f"{status.size_gb:.2f} GB" if status.installed and status.size_gb else "Not downloaded"
        revision = f"<span class='revision'>version {html.escape((status.revision or '')[:10])}</span>" if status.revision else ""
        update = "<span class='pill update'>Update available</span>" if status.update_available else ""
        cards.append(
            f"""
            <article class="model-card">
              <div class="model-card-head">
                <div><h3>{html.escape(model_ui_name(model_id))}</h3><p>{html.escape(MODEL_UI_DESCRIPTIONS[model_id])}</p></div>
                <span class="pill {'ready' if status.installed else 'missing'}">{html.escape(state)}</span>
              </div>
              <div class="model-meta"><span>{html.escape(size)}</span>{revision}{update}</div>
            </article>
            """
        )
    return "<div class='model-grid'>" + "".join(cards) + "</div>"


def refresh_models():
    installed = sum(1 for status in model_manager.statuses() if status.installed)
    return _model_cards_html(), f"{installed}/3 models installed."


def install_model_ui(model_ui: str, offline_mode: bool, progress=gr.Progress()):
    model_id = model_id_from_ui_name(model_ui)
    if model_id is None:
        raise gr.Error("Choose a model to install.")
    try:
        status = model_manager.download(
            model_id,
            offline=bool(offline_mode),
            progress=_model_download_progress(progress),
        )
        progress(None)
        return _model_cards_html(), f"✅ **{model_ui_name(model_id)}** is ready · {status.size_gb:.2f} GB."
    except Exception as exc:
        progress(None)
        raise gr.Error(_friendly_error(exc)) from exc


def check_model_update_ui(model_ui: str, offline_mode: bool, progress=gr.Progress()):
    model_id = model_id_from_ui_name(model_ui)
    if model_id is None:
        raise gr.Error("Choose a model first.")
    try:
        progress((0, None), desc="Checking for updates…")
        status = model_manager.check_update(model_id, offline=bool(offline_mode))
        progress(None)
        if not status.installed:
            note = "Install this model first."
        elif status.update_available:
            note = f"🔵 A newer **{model_ui_name(model_id)}** snapshot is available. Your current version stays selected until you press Update."
        elif status.remote_revision:
            note = f"✅ **{model_ui_name(model_id)}** is up to date."
        else:
            note = "Could not check right now. Your installed version is unchanged."
        return _model_cards_html(model_id, status), note
    except Exception as exc:
        progress(None)
        raise gr.Error(_friendly_error(exc)) from exc


def update_model_ui(model_ui: str, offline_mode: bool, progress=gr.Progress()):
    model_id = model_id_from_ui_name(model_ui)
    if model_id is None:
        raise gr.Error("Choose a model first.")
    try:
        status = model_manager.update(model_id, offline=bool(offline_mode), progress=_model_download_progress(progress))
        if engine.loaded_model_id == model_id:
            engine.unload()
        progress(None)
        return _model_cards_html(), f"✅ Updated **{model_ui_name(model_id)}**. The previous working version was never replaced until this action."
    except Exception as exc:
        progress(None)
        raise gr.Error(_friendly_error(exc)) from exc


def remove_model_ui(model_ui: str):
    model_id = model_id_from_ui_name(model_ui)
    if model_id is None:
        return _model_cards_html(), "Choose a model first."
    try:
        if engine.loaded_model_id == model_id:
            engine.unload()
        removed = model_manager.remove(model_id)
        return _model_cards_html(), f"{'Removed' if removed else 'No local files found for'} **{model_ui_name(model_id)}**. Voices and projects were not touched."
    except Exception as exc:
        return _model_cards_html(), f"❌ {_friendly_error(exc)}"


def load_model_ui(model_ui: str, compute_preference: str, offline_mode: bool, auto_download_models: bool, progress=gr.Progress()):
    model_id = model_id_from_ui_name(model_ui)
    if model_id is None:
        raise gr.Error("Choose a specific model first.")
    try:
        _configure_engine(model_id, compute_preference, offline_mode, auto_download_models, progress)
        engine.load_model(model_id, progress_callback=_engine_progress(progress))
        progress(None)
        return _model_cards_html(), f"✅ **{model_ui_name(model_id)}** is loaded and ready."
    except Exception as exc:
        progress(None)
        raise gr.Error(_friendly_error(exc)) from exc


def unload_model_ui():
    previous = engine.loaded_model_id
    engine.unload()
    return _model_cards_html(), (f"Unloaded **{model_ui_name(previous)}** from memory." if previous else "No model is loaded right now.")


def refresh_hardware():
    global hardware
    hardware = collect_hardware_profile()
    return hardware_summary(hardware), format_diagnostics(collect_diagnostics(ROOT))


def save_user_preferences(compute_preference, quality_mode, offline_mode, auto_download_models, trim_silence, peak_normalize, fade_ms):
    set_hf_offline(bool(offline_mode))
    _save_settings(
        {
            "compute_preference": compute_preference,
            "generation_quality": quality_mode,
            "offline_mode": bool(offline_mode),
            "auto_download_models": bool(auto_download_models),
            "trim_silence": bool(trim_silence),
            "peak_normalize": bool(peak_normalize),
            "fade_ms": int(fade_ms),
        }
    )
    return "✅ Settings saved."


def speech_tools_status() -> str:
    available = importlib.util.find_spec("faster_whisper") is not None
    return "✅ Speech tools are installed." if available else "Speech to Text is optional. Install the local speech tools once if you want Transcribe or STT verification."


def install_speech_tools(progress=gr.Progress()):
    requirements = ROOT / "requirements-optional.txt"
    if not requirements.exists():
        raise gr.Error("Optional requirements file is missing.")
    try:
        progress((0, None), desc="Installing local speech tools…")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        progress(None)
        if result.returncode != 0:
            lines = (result.stderr or result.stdout or "Installation failed.").strip().splitlines()
            raise RuntimeError(lines[-1] if lines else "Installation failed.")
        importlib.invalidate_caches()
        return "✅ Speech tools installed. Transcribe is ready."
    except Exception as exc:
        progress(None)
        raise gr.Error(_friendly_error(exc)) from exc


def footer_html() -> str:
    return """
    <div class="footer-note">
      <span>Runs locally by default.</span>
      <span>Current speech models are open-source Chatterbox models; credits and licenses are in About.</span>
    </div>
    """


available_voices = _voice_choices()
initial_voice = settings.get("voice") if settings.get("voice") in available_voices else (available_voices[0] if available_voices else None)
project_ids = _project_choices()
initial_project = settings.get("project") if settings.get("project") in project_ids else (project_ids[0] if project_ids else None)
initial_model_ui = settings.get("model_selection") if settings.get("model_selection") in MODEL_SELECTIONS else AUTO_MODEL
initial_language_ui = settings.get("language_selection") if settings.get("language_selection") in LANGUAGE_CHOICES else "Auto"
initial_preset = settings.get("preset") if settings.get("preset") in PRESETS else "Creator"
initial_quality = settings.get("generation_quality") if settings.get("generation_quality") in QUALITY_MODES else "Balanced"
initial_compute = settings.get("compute_preference") if settings.get("compute_preference") in COMPUTE_CHOICES else "Auto"
initial_quality_policy = quality_policy(initial_quality)

with gr.Blocks(title="Creator Studio", analytics_enabled=False) as demo:
    gr.HTML(
        """
        <header class="app-header">
          <div class="brand-mark" aria-hidden="true">◌</div>
          <div class="brand-copy"><strong>Creator Studio</strong><span>Local voice workspace</span></div>
          <div class="local-badge">Local</div>
        </header>
        """
    )

    with gr.Tabs(elem_id="product-nav"):
        with gr.Tab("Create"):
            with gr.Row(equal_height=False):
                with gr.Column(scale=8, elem_classes="workspace-card"):
                    with gr.Row():
                        project_dropdown = gr.Dropdown(label="Project", choices=project_ids, value=initial_project, allow_custom_value=False, scale=4)
                        project_refresh = gr.Button("↻", size="sm", scale=0, min_width=44)
                    script = gr.Textbox(
                        label="Script",
                        value=SAMPLE_SCRIPT,
                        lines=15,
                        max_lines=30,
                        placeholder="Write or paste your script…",
                        elem_id="script-box",
                    )
                    with gr.Row(elem_classes="quick-row"):
                        pause_025 = gr.Button("Pause 0.25s", size="sm", variant="secondary")
                        pause_05 = gr.Button("Pause 0.5s", size="sm", variant="secondary")
                        pause_1 = gr.Button("Pause 1s", size="sm", variant="secondary")
                        pause_2 = gr.Button("Pause 2s", size="sm", variant="secondary")
                    with gr.Row():
                        generate_btn = gr.Button("Generate", variant="primary", size="lg", elem_id="generate-btn")
                        compare_btn = gr.Button("Compare models", variant="secondary", size="lg", elem_id="compare-btn")
                    create_status = gr.Markdown("Ready when you are.", elem_classes="status-line")
                    output_audio = gr.Audio(label="Result", interactive=False, format="wav", elem_classes="result-audio")
                    with gr.Row(elem_classes="result-actions"):
                        output_download = gr.DownloadButton("Download audio", variant="secondary")
                    with gr.Accordion("Details", open=False):
                        generation_details = gr.Markdown("Generation details appear here.")
                        metadata_download = gr.DownloadButton("Download generation details", variant="secondary")
                        with gr.Row():
                            project_takes = gr.Dropdown(label="Project takes", choices=[])
                            takes_refresh = gr.Button("Refresh", size="sm")
                            take_load = gr.Button("Load", size="sm")

                    with gr.Accordion("Compare results", open=False):
                        compare_status = gr.Markdown("One click compares every compatible model. Models are released between runs when that is safer for memory.")
                        with gr.Row():
                            with gr.Column(elem_classes="compare-card"):
                                gr.Markdown("### Multilingual")
                                compare_v3_audio = gr.Audio(interactive=False, visible=False, label="Multilingual")
                                compare_v3_download = gr.DownloadButton("Download", visible=False)
                            with gr.Column(elem_classes="compare-card"):
                                gr.Markdown("### Expressive")
                                compare_turbo_audio = gr.Audio(interactive=False, visible=False, label="Expressive")
                                compare_turbo_download = gr.DownloadButton("Download", visible=False)
                            with gr.Column(elem_classes="compare-card"):
                                gr.Markdown("### Light")
                                compare_nano_audio = gr.Audio(interactive=False, visible=False, label="Light")
                                compare_nano_download = gr.DownloadButton("Download", visible=False)

                with gr.Column(scale=4, elem_classes="control-card"):
                    gr.Markdown("### Voice")
                    voice_dropdown = gr.Dropdown(label="Voice", choices=available_voices, value=initial_voice, allow_custom_value=False)
                    with gr.Accordion("+ Add a voice", open=not bool(available_voices)):
                        quick_voice_audio = gr.Audio(label="Drop a recording or record now", sources=["upload", "microphone"], type="filepath", format="wav")
                        quick_voice_name = gr.Textbox(label="Name", placeholder="My voice")
                        quick_voice_save = gr.Button("Save & use", variant="primary")
                        quick_voice_status = gr.Markdown("A clean 8–15 second recording usually works well.")

                    gr.Markdown("### Sound")
                    model_ui = gr.Dropdown(label="Model", choices=list(MODEL_SELECTIONS), value=initial_model_ui, allow_custom_value=False, info="Auto chooses a sensible model for the language and this computer.")
                    language_ui = gr.Dropdown(label="Language", choices=LANGUAGE_CHOICES, value=initial_language_ui, allow_custom_value=False)
                    preset = gr.Dropdown(label="Style", choices=list(PRESETS), value=initial_preset)
                    speech_speed = gr.Slider(0.75, 1.25, value=float(settings["speech_speed"]), step=0.01, label="Speed")
                    model_state = gr.Markdown(_model_state_line(initial_model_ui, initial_language_ui, SAMPLE_SCRIPT, initial_compute), elem_classes="model-choice-note")
                    gr.Markdown("<div class='soft-note'>Need more control? Everything technical lives in <b>Settings → Expert</b>, not in your way here.</div>")

        with gr.Tab("Library"):
            with gr.Tabs():
                with gr.Tab("Voices"):
                    with gr.Row(equal_height=False):
                        with gr.Column(scale=2, elem_classes="workspace-card"):
                            library_voice_audio = gr.Audio(label="Add a voice", sources=["upload", "microphone"], type="filepath", format="wav")
                            library_voice_name = gr.Textbox(label="Name", placeholder="Narrator")
                            library_voice_save = gr.Button("Save voice", variant="primary")
                            library_voice_refresh = gr.Button("Refresh")
                        with gr.Column(scale=3, elem_classes="workspace-card"):
                            voice_manage = gr.Dropdown(label="Saved voices", choices=available_voices, value=initial_voice)
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
                            gr.Markdown("Create a project when you want to keep multiple takes together. The Create page also saves changes to an open project automatically.")

                with gr.Tab("History"):
                    with gr.Row(equal_height=False):
                        with gr.Column(scale=2, elem_classes="workspace-card"):
                            history = gr.Dropdown(label="Recent generations", choices=engine.recent_outputs())
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
                            stt_quality = gr.Dropdown(label="Quality", choices=list(QUALITY_MODES), value=initial_quality)
                            stt_language = gr.Dropdown(label="Language", choices=LANGUAGE_CHOICES, value="Auto")
                            stt_btn = gr.Button("Transcribe", variant="primary")
                            stt_status = gr.Markdown(speech_tools_status())
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
                            batch_model = gr.Dropdown(label="Model", choices=list(MODEL_SELECTIONS), value=initial_model_ui)
                            batch_language = gr.Dropdown(label="Language", choices=LANGUAGE_CHOICES, value=initial_language_ui)
                            batch_fit = gr.Checkbox(label="Fit subtitle clips to cue timing", value=bool(settings["batch_fit_timing"]))
                            batch_stretch = gr.Slider(1.02, 1.5, value=float(settings["batch_max_stretch"]), step=0.01, label="Maximum safe timing adjustment")
                            batch_run = gr.Button("Generate batch", variant="primary")
                            batch_manifest = gr.DownloadButton("Download manifest")
                            batch_output_dir = gr.Textbox(label="Output folder", interactive=False)
                            batch_status = gr.Markdown()
                        with gr.Column(scale=4, elem_classes="workspace-card"):
                            batch_table = gr.Dataframe(headers=["id", "text", "start", "end", "target duration"], datatype=["str", "str", "number", "number", "number"], interactive=False, wrap=True, label="Items")

        with gr.Tab("Models"):
            gr.Markdown("## Models")
            gr.Markdown("Install what you want. The app keeps your selected local snapshot until **you** choose to update it.")
            models_html = gr.HTML(_model_cards_html())
            with gr.Row(equal_height=False):
                with gr.Column(scale=2, elem_classes="workspace-card"):
                    model_action = gr.Dropdown(label="Model", choices=[MODEL_UI_NAMES[mid] for mid in MODEL_SPECS], value=MODEL_UI_NAMES["nano"])
                    with gr.Row():
                        model_install = gr.Button("Install", variant="primary")
                        model_load = gr.Button("Load")
                    with gr.Row():
                        model_check = gr.Button("Check update")
                        model_update = gr.Button("Update")
                    with gr.Row():
                        model_unload = gr.Button("Unload current")
                        model_remove = gr.Button("Remove", variant="stop")
                    model_refresh = gr.Button("Refresh")
                    model_action_status = gr.Markdown()
                with gr.Column(scale=3, elem_classes="workspace-card"):
                    gr.Markdown("### This computer")
                    hardware_box = gr.Markdown(hardware_summary(hardware), elem_classes="hardware-card")
                    gr.Markdown("Auto uses the fastest available backend that this installation can actually use. You can override it in Settings.")

        with gr.Tab("Settings"):
            with gr.Row(equal_height=False):
                with gr.Column(scale=2, elem_classes="workspace-card"):
                    gr.Markdown("## Simple defaults")
                    compute_preference = gr.Dropdown(label="Performance", choices=list(COMPUTE_CHOICES), value=initial_compute, info="Auto is recommended.")
                    quality_mode = gr.Dropdown(label="Generation quality", choices=list(QUALITY_MODES), value=initial_quality)
                    offline_mode = gr.Checkbox(label="Offline mode", value=bool(settings.get("offline_mode", False)), info="Never download a missing model while this is on.")
                    auto_download_models = gr.Checkbox(label="Download a missing selected model when needed", value=bool(settings.get("auto_download_models", True)))
                    gr.Markdown("### Audio finishing")
                    trim_silence = gr.Checkbox(label="Trim empty silence at the ends", value=bool(settings["trim_silence"]))
                    peak_normalize = gr.Checkbox(label="Normalize output level", value=bool(settings["peak_normalize"]))
                    fade_ms = gr.Slider(0, 250, value=int(settings["fade_ms"]), step=5, label="Short fade (ms)")
                    save_preferences = gr.Button("Save settings", variant="primary")
                    preferences_status = gr.Markdown()

                    gr.Markdown("### Speech tools")
                    speech_tools_note = gr.Markdown(speech_tools_status())
                    install_speech_btn = gr.Button("Install speech tools")

                with gr.Column(scale=3, elem_classes="workspace-card"):
                    with gr.Accordion("Expert", open=False):
                        gr.Markdown("These controls are optional. The Create page does not require you to understand them.")
                        raw_mode = gr.Checkbox(label="Raw model text path", value=bool(settings["raw_mode"]), info="Bypasses Studio pause parsing, chunking, and text cleanup.")
                        smart_chunking = gr.Checkbox(label="Split long text automatically", value=bool(settings["smart_chunking"]))
                        max_chars = gr.Slider(80, 500, value=int(settings["max_chars"]), step=10, label="Target characters per chunk")
                        chunk_gap = gr.Slider(0.0, 1.0, value=float(settings["chunk_gap_seconds"]), step=0.01, label="Gap between chunks (seconds)")
                        seed = gr.Number(value=int(settings["seed"]), precision=0, label="Seed", info="-1 creates a fresh seed and records it.")

                        gr.Markdown("#### Voice generation")
                        exaggeration = gr.Slider(0.0, 1.5, value=float(settings["exaggeration"]), step=0.05, label="Expression strength")
                        cfg_weight = gr.Slider(0.0, 1.0, value=float(settings["cfg_weight"]), step=0.05, label="Pacing guidance")
                        temperature = gr.Slider(0.05, 1.5, value=float(settings["temperature"]), step=0.05, label="Variation")
                        repetition_penalty = gr.Slider(1.0, 2.0, value=float(settings["repetition_penalty"]), step=0.05, label="Repetition control")
                        min_p = gr.Slider(0.0, 0.5, value=float(settings["min_p"]), step=0.01, label="Min P")
                        top_p = gr.Slider(0.1, 1.0, value=float(settings["top_p"]), step=0.01, label="Top P")
                        top_k = gr.Slider(50, 2000, value=int(settings["top_k"]), step=50, label="Top K")

                        gr.Markdown("#### Text cleanup")
                        normalize_unicode = gr.Checkbox(label="Normalize Unicode", value=bool(settings["normalize_unicode"]))
                        normalize_punctuation = gr.Checkbox(label="Normalize punctuation", value=bool(settings["normalize_punctuation"]))
                        normalize_numbers = gr.Checkbox(label="Read numbers as words", value=bool(settings["normalize_numbers"]))
                        replace_urls = gr.Checkbox(label="Replace URLs with the word URL", value=bool(settings["replace_urls"]))
                        collapse_punctuation = gr.Checkbox(label="Collapse extreme repeated punctuation", value=bool(settings["collapse_repeated_punctuation"]))
                        normalize_whitespace = gr.Checkbox(label="Normalize spaces", value=bool(settings["normalize_whitespace"]))
                        preview_btn = gr.Button("Preview processed text")
                        preview_text = gr.Textbox(label="Processed text", lines=6, interactive=False)
                        preview_note = gr.Markdown()

                        gr.Markdown("#### Reliability")
                        quality_check = gr.Checkbox(label="Check generated audio automatically", value=bool(initial_quality_policy["quality_check"]))
                        auto_retries = gr.Slider(0, 3, value=int(initial_quality_policy["auto_retries"]), step=1, label="Automatic retries")
                        best_of_n = gr.Slider(1, 3, value=int(initial_quality_policy["best_of_n"]), step=1, label="Generate candidates and keep the best")
                        verify_stt = gr.Checkbox(label="Verify generated speech with local STT", value=bool(settings["verify_stt"]))
                        whisper_model = gr.Dropdown(label="STT verifier size", choices=["tiny", "base", "small"], value=str(settings["whisper_model"]))
                        verification_threshold = gr.Slider(0.5, 0.98, value=float(settings["verification_threshold"]), step=0.01, label="Transcript similarity threshold")

                    with gr.Accordion("Diagnostics", open=False):
                        diagnostics_box = gr.Code(value=format_diagnostics(collect_diagnostics(ROOT)), language="json", label="Technical diagnostics", interactive=False)
                        diagnostics_refresh = gr.Button("Refresh diagnostics")

                    with gr.Accordion("About & open-source credits", open=False):
                        gr.Markdown(
                            """
                            This is an independent local creator application. The current speech engines are the open-source **Chatterbox Multilingual V3, Turbo, and Nano** models and implementation by **Resemble AI**. This application is not an official Resemble AI product and is not endorsed by Resemble AI.

                            Full source notices and license information are kept in `README.md`, `NOTICE.md`, and `LICENSE`. The app does not remove the upstream audio watermarking behavior.
                            """
                        )

    gr.HTML(footer_html())

    preset.change(
        apply_preset,
        inputs=preset,
        outputs=[exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, speech_speed],
        queue=False,
    )
    quality_mode.change(apply_quality_mode, inputs=quality_mode, outputs=[quality_check, auto_retries, best_of_n], queue=False)

    for component in (model_ui, language_ui, script, compute_preference):
        component.change(_model_state_line, inputs=[model_ui, language_ui, script, compute_preference], outputs=model_state, queue=False)

    def append_token(text: str, token: str) -> str:
        text = text or ""
        if not text:
            return token
        return f"{text.rstrip()} {' ' if not token.startswith('[pause=') else ''}{token}"

    pause_025.click(lambda text: append_token(text, "[pause=0.25]"), inputs=script, outputs=script, queue=False)
    pause_05.click(lambda text: append_token(text, "[pause=0.5]"), inputs=script, outputs=script, queue=False)
    pause_1.click(lambda text: append_token(text, "[pause=1]"), inputs=script, outputs=script, queue=False)
    pause_2.click(lambda text: append_token(text, "[pause=2]"), inputs=script, outputs=script, queue=False)

    voice_outputs = [voice_dropdown, voice_manage, batch_voice, quick_voice_audio, quick_voice_status]
    quick_voice_save.click(add_voice, inputs=[quick_voice_audio, quick_voice_name], outputs=voice_outputs, show_progress="minimal")
    library_voice_save.click(add_voice, inputs=[library_voice_audio, library_voice_name], outputs=[voice_dropdown, voice_manage, batch_voice, library_voice_audio, voice_manage_status], show_progress="minimal")
    library_voice_refresh.click(refresh_voices, outputs=[voice_dropdown, voice_manage, batch_voice], queue=False)
    voice_manage.change(inspect_voice, inputs=voice_manage, outputs=[voice_inspection, voice_preview], queue=False)
    voice_inspect.click(inspect_voice, inputs=voice_manage, outputs=[voice_inspection, voice_preview], queue=False)
    rename_voice_btn.click(rename_voice, inputs=[voice_manage, rename_voice_name], outputs=[voice_manage, voice_manage_status], queue=False).then(sync_voice_selection, inputs=voice_manage, outputs=[voice_dropdown, voice_manage, batch_voice], queue=False)
    duplicate_voice_btn.click(duplicate_voice, inputs=voice_manage, outputs=[voice_manage, voice_manage_status], queue=False).then(sync_voice_selection, inputs=voice_manage, outputs=[voice_dropdown, voice_manage, batch_voice], queue=False)
    delete_voice_btn.click(delete_voice, inputs=voice_manage, outputs=[voice_manage, voice_manage_status], queue=False).then(sync_voice_selection, inputs=voice_manage, outputs=[voice_dropdown, voice_manage, batch_voice], queue=False)

    preprocess_inputs = [script, language_ui, raw_mode, normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls, collapse_punctuation, normalize_whitespace]
    preview_btn.click(preview_processed_text, inputs=preprocess_inputs, outputs=[preview_text, preview_note], queue=False)

    generation_inputs = [
        script, project_dropdown, voice_dropdown, model_ui, language_ui, preset, speech_speed,
        compute_preference, quality_mode, offline_mode, auto_download_models,
        exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, top_k,
        raw_mode, smart_chunking, max_chars, chunk_gap, seed,
        normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls, collapse_punctuation, normalize_whitespace,
        quality_check, verify_stt, whisper_model, verification_threshold, auto_retries, best_of_n,
        trim_silence, peak_normalize, fade_ms,
    ]
    generate_btn.click(
        generate_audio,
        inputs=generation_inputs,
        outputs=[output_audio, output_download, metadata_download, create_status, generation_details, history, project_takes, model_state],
        show_progress="minimal",
    )

    compare_inputs = [
        script, project_dropdown, voice_dropdown, language_ui, preset, speech_speed,
        compute_preference, quality_mode, offline_mode, auto_download_models,
        exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, top_k,
        raw_mode, smart_chunking, max_chars, chunk_gap, seed,
        normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls, collapse_punctuation, normalize_whitespace,
        quality_check, verify_stt, whisper_model, verification_threshold, auto_retries, best_of_n,
        trim_silence, peak_normalize, fade_ms,
    ]
    compare_btn.click(
        compare_models,
        inputs=compare_inputs,
        outputs=[compare_v3_audio, compare_v3_download, compare_turbo_audio, compare_turbo_download, compare_nano_audio, compare_nano_download, compare_status],
        show_progress="minimal",
    )

    project_refresh.click(refresh_projects, outputs=project_dropdown, queue=False)
    create_project_btn.click(create_project, inputs=project_name, outputs=[project_dropdown, project_status], queue=False)
    load_project_btn.click(load_project, inputs=project_dropdown, outputs=[script, voice_dropdown, model_ui, language_ui, project_name, project_status], queue=False)
    save_project_btn.click(save_project, inputs=[project_dropdown, project_name, script, voice_dropdown, model_ui, language_ui, compute_preference], outputs=project_status, queue=False)
    delete_project_btn.click(delete_project, inputs=project_dropdown, outputs=[project_dropdown, project_status], queue=False)
    takes_refresh.click(project_take_choices, inputs=project_dropdown, outputs=project_takes, queue=False)
    take_load.click(load_take, inputs=project_takes, outputs=[output_audio, output_download], queue=False)
    if initial_project:
        demo.load(project_take_choices, inputs=project_dropdown, outputs=project_takes, queue=False)

    for component in (script, voice_dropdown, model_ui, language_ui):
        component.change(
            autosave_project,
            inputs=[project_dropdown, script, voice_dropdown, model_ui, language_ui, compute_preference],
            outputs=project_status,
            queue=False,
        )

    history_refresh.click(refresh_history, outputs=history, queue=False)
    history_load.click(load_history, inputs=history, outputs=[history_audio, history_download, history_metadata, history_note], queue=False)
    history_delete.click(delete_history, inputs=history, outputs=[history, history_note], queue=False)

    batch_parse.click(preview_batch_file, inputs=batch_file, outputs=[batch_state, batch_table, batch_status], queue=False)
    batch_inputs = [
        batch_state, batch_voice, batch_model, batch_language, speech_speed, compute_preference, offline_mode, auto_download_models,
        exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, top_k,
        raw_mode, smart_chunking, max_chars, chunk_gap, seed,
        normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls, collapse_punctuation, normalize_whitespace,
        quality_check, verify_stt, whisper_model, verification_threshold, auto_retries, best_of_n,
        batch_fit, batch_stretch,
    ]
    batch_run.click(run_batch_ui, inputs=batch_inputs, outputs=[batch_manifest, batch_output_dir, batch_status], show_progress="minimal")

    stt_btn.click(transcribe_ui, inputs=[stt_audio, stt_quality, stt_language, compute_preference], outputs=[stt_text, stt_segments, stt_status], show_progress="minimal")

    model_refresh.click(refresh_models, outputs=[models_html, model_action_status], queue=False)
    model_install.click(install_model_ui, inputs=[model_action, offline_mode], outputs=[models_html, model_action_status], show_progress="minimal")
    model_check.click(check_model_update_ui, inputs=[model_action, offline_mode], outputs=[models_html, model_action_status], show_progress="minimal")
    model_update.click(update_model_ui, inputs=[model_action, offline_mode], outputs=[models_html, model_action_status], show_progress="minimal")
    model_remove.click(remove_model_ui, inputs=model_action, outputs=[models_html, model_action_status], queue=False)
    model_load.click(load_model_ui, inputs=[model_action, compute_preference, offline_mode, auto_download_models], outputs=[models_html, model_action_status], show_progress="minimal")
    model_unload.click(unload_model_ui, outputs=[models_html, model_action_status], queue=False)
    demo.load(refresh_models, outputs=[models_html, model_action_status], queue=False)

    save_preferences.click(
        save_user_preferences,
        inputs=[compute_preference, quality_mode, offline_mode, auto_download_models, trim_silence, peak_normalize, fade_ms],
        outputs=preferences_status,
        queue=False,
    )
    install_speech_btn.click(install_speech_tools, outputs=speech_tools_note, show_progress="minimal")
    diagnostics_refresh.click(refresh_hardware, outputs=[hardware_box, diagnostics_box], queue=False)


def launch():
    host = os.getenv("CHATTERBOX_STUDIO_HOST", "127.0.0.1")
    port = int(os.getenv("CHATTERBOX_STUDIO_PORT", "7860"))
    share = os.getenv("CHATTERBOX_STUDIO_SHARE", "0").strip().lower() in {"1", "true", "yes"}
    css = CSS_PATH.read_text(encoding="utf-8") if CSS_PATH.exists() else None
    demo.queue(default_concurrency_limit=1).launch(
        server_name=host,
        server_port=port,
        share=share,
        inbrowser=False,
        css=css,
        show_error=True,
    )


if __name__ == "__main__":
    launch()
