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


def _storage_root() -> Path:
    """Return persistent writable storage without changing the developer workflow.

    Source/development runs continue to use the repository's data/ and outputs/
    directories. Frozen desktop builds use the user's application-data directory so
    an app update/uninstall cannot accidentally treat voices and projects as program
    files. Tests or portable builds may override the location explicitly.
    """
    override = os.getenv("CREATOR_STUDIO_DATA_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        if base:
            return Path(base) / "CreatorStudio"
        return Path.home() / ".creator-studio"
    return ROOT


STORAGE_ROOT = _storage_root()
DATA_DIR = STORAGE_ROOT / "data"
VOICE_DIR = DATA_DIR / "voices"
PROJECT_DIR = DATA_DIR / "projects"
OUTPUT_DIR = STORAGE_ROOT / "outputs"
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


def _model_cards_html() -> str:
    cards: list[str] = []
    for model_id in MODEL_SPECS:
        status = model_manager.status(model_id)
        state = human_model_status(status)
        size = f" · {status.size_gb:.2f} GB" if status.installed else ""
        loaded = " · Loaded" if engine.loaded_model_id == model_id else ""
        cards.append(
            f"<div class='model-card'><div class='model-card-title'><b>{html.escape(model_ui_name(model_id))}</b><span>{html.escape(state)}</span></div>"
            f"<div class='model-card-copy'>{html.escape(MODEL_UI_DESCRIPTIONS[model_ui_name(model_id)])}</div>"
            f"<div class='model-card-meta'>{html.escape(model_detail(model_id))}{html.escape(size)}{html.escape(loaded)}</div></div>"
        )
    return "".join(cards)


def refresh_models():
    return _model_cards_html(), "Model list refreshed."


def _diagnostics_snapshot() -> dict:
    payload = collect_diagnostics(ROOT)
    payload["storage_root"] = str(STORAGE_ROOT)
    payload["data_dir"] = str(DATA_DIR)
    payload["output_dir"] = str(OUTPUT_DIR)
    payload["hardware"] = hardware.as_dict()
    payload["model_cache"] = [row.__dict__ for row in model_manager.list_statuses()]
    return payload


def refresh_hardware():
    global hardware
    hardware = collect_hardware_profile()
    return hardware_summary(hardware), format_diagnostics(_diagnostics_snapshot())


def _save_settings_snapshot(**values) -> None:
    payload = settings_store.load()
    payload.update(values)
    settings_store.save(payload)


def save_user_preferences(compute_preference, quality_mode, offline_mode, auto_download_models, trim_silence, peak_normalize, fade_ms):
    _save_settings_snapshot(
        compute_preference=compute_preference,
        generation_quality=quality_mode,
        offline_mode=bool(offline_mode),
        auto_download_models=bool(auto_download_models),
        trim_silence=bool(trim_silence),
        peak_normalize=bool(peak_normalize),
        fade_ms=int(fade_ms),
    )
    set_hf_offline(bool(offline_mode))
    return "✅ Settings saved."


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
    preset,
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
        model_id, language_name, _, device_label = _resolve_choice(model_ui, language_ui, script, compute_preference)
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
        metadata_path = selected.result.metadata_path
        if project_id:
            try:
                projects.add_take(project_id, final_audio, metadata_path, segment_id="main")
            except Exception:
                pass
        history_choices = engine.recent_outputs()
        take_choices = [str(path) for path in projects.list_takes(project_id)] if project_id else []
        progress(None)
        status = f"✅ **Ready** · {model_ui_name(model_id)} · {language_name} · {device_label}"
        detail = (
            f"Model: **{model_ui_name(model_id)}**  \n"
            f"Language: **{language_name}**  \n"
            f"Performance: **{device_label}**  \n"
            f"Quality: **{quality_mode}**  \n"
            f"Seed: **{selected.result.seed}**  \n"
            f"Attempts: **{len(reliable.candidates)}**  \n"
            f"Selected attempt: **{selected.index + 1}**"
        )
        _save_settings_snapshot(
            voice=voice_name,
            project=project_id,
            model_id=model_id,
            model_selection=model_ui,
            language=language_code_from_name(language_name),
            language_selection=language_ui,
            preset=preset,
            generation_quality=quality_mode,
            compute_preference=compute_preference,
            speech_speed=float(speech_speed),
            exaggeration=float(exaggeration),
            cfg_weight=float(cfg_weight),
            temperature=float(temperature),
            repetition_penalty=float(repetition_penalty),
            min_p=float(min_p),
            top_p=float(top_p),
            top_k=int(top_k),
            raw_mode=bool(raw_mode),
            smart_chunking=bool(smart_chunking),
            max_chars=int(max_chars),
            chunk_gap_seconds=float(chunk_gap_seconds),
            seed=int(selected.result.seed),
            normalize_unicode=bool(normalize_unicode),
            normalize_punctuation=bool(normalize_punctuation),
            normalize_numbers=bool(normalize_numbers),
            replace_urls=bool(replace_urls),
            collapse_repeated_punctuation=bool(collapse_punctuation),
            normalize_whitespace=bool(normalize_whitespace),
            quality_check=bool(quality_check),
            verify_stt=bool(verify_stt),
            whisper_model=whisper_model,
            verification_threshold=float(verification_threshold),
            auto_retries=int(auto_retries),
            best_of_n=int(best_of_n),
            trim_silence=bool(trim_silence),
            peak_normalize=bool(peak_normalize),
            fade_ms=int(fade_ms),
        )
        return (
            str(final_audio),
            str(final_audio),
            str(metadata_path),
            status,
            detail,
            gr.update(choices=history_choices, value=str(final_audio)),
            gr.update(choices=take_choices, value=take_choices[0] if take_choices else None),
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
        model_ids = safe_compare_order(compatible_models(language_ui, script))
        language_name = resolve_language(language_ui, script)
        outputs: dict[str, str | None] = {"multilingual-v3": None, "turbo": None, "nano": None}
        completed = []
        total = len(model_ids)
        for index, model_id in enumerate(model_ids, 1):
            progress((index - 1, total), desc=f"Comparing {model_ui_name(model_id)}…", unit="model")
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
                progress_callback=_engine_progress(progress, prefix=f"{model_ui_name(model_id)} · "),
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
            outputs[model_id] = str(final_audio)
            completed.append(model_ui_name(model_id))
            if project_id:
                try:
                    projects.add_take(project_id, final_audio, selected.result.metadata_path, segment_id=f"compare-{model_id}")
                except Exception:
                    pass
            engine.unload()
        progress(None)
        return (
            outputs["multilingual-v3"],
            outputs["multilingual-v3"],
            outputs["turbo"],
            outputs["turbo"],
            outputs["nano"],
            outputs["nano"],
            "✅ Compared: " + " · ".join(completed),
        )
    except Exception as exc:
        progress(None)
        engine.unload()
        raise gr.Error(_friendly_error(exc)) from exc


def preview_batch_file(path: str | None):
    if not path:
        return [], [], "Choose a TXT, Markdown, CSV, JSON, SRT, or VTT file."
    try:
        items = load_batch(path)
        return [item.__dict__ for item in items], preview_rows(items), f"✅ Loaded **{len(items)}** items."
    except Exception as exc:
        return [], [], f"❌ {_friendly_error(exc)}"


def run_batch_ui(
    items_state,
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
    if not items_state:
        raise gr.Error("Load a batch file first.")
    try:
        items = [BatchItem(**item) if isinstance(item, dict) else item for item in items_state]
        model_id, language_name, _, _ = _resolve_choice(model_ui, language_ui, items[0].text, compute_preference)
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
        batch_result = run_batch(
            engine,
            items,
            BATCH_DIR,
            kwargs,
            fit_timing=bool(fit_timing),
            max_stretch=float(max_stretch),
            progress_callback=_batch_progress(progress),
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
        )
        progress(None)
        return str(batch_result.manifest_path), str(batch_result.output_dir), f"✅ Generated **{len(batch_result.items)}** items."
    except Exception as exc:
        progress(None)
        raise gr.Error(_friendly_error(exc)) from exc


def _batch_progress(current: int, total: int, desc: str) -> None:
    # Bound later by UI callback where Gradio provides the progress object.
    return None


def transcribe_ui(audio_path, quality_mode, language_ui, compute_preference, progress=gr.Progress()):
    if not audio_path:
        raise gr.Error("Choose or record audio first.")
    try:
        language_name = resolve_language(language_ui, "") if language_ui != "Auto" else "Auto"
        language_code = None if language_name == "Auto" else language_code_from_name(language_name)
        _, device, _ = _product_system_profile(compute_preference)
        model_size = {"Fast": "tiny", "Balanced": "base", "Best": "small"}.get(quality_mode, "base")
        progress((0, None), desc="Listening…")
        result = transcribe_audio(audio_path, model_size=model_size, language=language_code, device="cuda" if device == "cuda" else "cpu")
        rows = [[round(seg.start, 2), round(seg.end, 2), seg.text] for seg in result.segments]
        progress(None)
        language_note = result.language or "auto"
        return result.text, rows, f"✅ Transcript ready · **{language_note}**"
    except Exception as exc:
        progress(None)
        raise gr.Error(_friendly_error(exc)) from exc


def speech_tools_status() -> str:
    if importlib.util.find_spec("faster_whisper") is not None:
        return "✅ Local speech tools are installed."
    return "Speech-to-text is optional. Install it only if you want Transcribe or transcript verification."


def install_speech_tools(progress=gr.Progress()):
    if importlib.util.find_spec("faster_whisper") is not None:
        return "✅ Local speech tools are already installed."
    requirements = ROOT / "requirements-optional.txt"
    if not requirements.exists():
        return "Optional requirements file is missing."
    progress((0, None), desc="Installing local speech tools…")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(requirements)], check=True)
        progress(None)
        return "✅ Speech tools installed. Restart the app once, then Transcribe is ready."
    except Exception as exc:
        progress(None)
        return f"❌ Could not install speech tools: {_friendly_error(exc)}"


def _history_entry_paths(entry: str | None) -> tuple[str | None, str | None]:
    if not entry:
        return None, None
    audio = Path(entry)
    metadata = audio.with_suffix(".json")
    return (str(audio) if audio.exists() else None, str(metadata) if metadata.exists() else None)


def refresh_history():
    choices = engine.recent_outputs()
    return gr.update(choices=choices, value=choices[0] if choices else None)


def load_history(entry: str | None):
    audio, metadata = _history_entry_paths(entry)
    if not audio:
        return None, None, None, "That generation is no longer available."
    return audio, audio, metadata, "Ready."


def delete_history(entry: str | None):
    if not entry:
        return gr.update(), "Choose a history item first."
    audio = Path(entry)
    metadata = audio.with_suffix(".json")
    for path in (audio, metadata):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
    choices = engine.recent_outputs()
    return gr.update(choices=choices, value=choices[0] if choices else None), "History item deleted."


def model_download_ui(model_ui, offline_mode, progress=gr.Progress()):
    model_id = model_id_from_ui_name(model_ui)
    if model_id is None:
        return _model_cards_html(), "Choose a specific model to install."
    try:
        status = model_manager.download(model_id, offline=bool(offline_mode), progress=_model_download_progress(progress))
        progress(None)
        return _model_cards_html(), f"✅ **{model_ui_name(model_id)}** installed · {status.size_gb:.2f} GB."
    except Exception as exc:
        progress(None)
        return _model_cards_html(), f"❌ {_friendly_error(exc)}"


def check_model_update_ui(model_ui, offline_mode, progress=gr.Progress()):
    model_id = model_id_from_ui_name(model_ui)
    if model_id is None:
        return _model_cards_html(), "Choose a specific model first."
    try:
        progress((0, None), desc="Checking for model update…")
        status = model_manager.check_update(model_id, offline=bool(offline_mode))
        progress(None)
        if not status.installed:
            note = f"**{model_ui_name(model_id)}** is not installed yet."
        elif status.update_available:
            note = f"An update is available for **{model_ui_name(model_id)}**. Nothing changes until you press Update."
        else:
            note = f"**{model_ui_name(model_id)}** is already on the selected snapshot."
        return _model_cards_html(), note
    except Exception as exc:
        progress(None)
        return _model_cards_html(), f"❌ {_friendly_error(exc)}"


def update_model_ui(model_ui, offline_mode, progress=gr.Progress()):
    model_id = model_id_from_ui_name(model_ui)
    if model_id is None:
        return _model_cards_html(), "Choose a specific model first."
    try:
        status = model_manager.update(model_id, offline=bool(offline_mode), progress=_model_download_progress(progress))
        if engine.loaded_model_id == model_id:
            engine.unload()
        progress(None)
        return _model_cards_html(), f"✅ **{model_ui_name(model_id)}** updated explicitly. Existing local projects and voices were not changed."
    except Exception as exc:
        progress(None)
        return _model_cards_html(), f"❌ {_friendly_error(exc)}"


def remove_model_ui(model_ui):
    model_id = model_id_from_ui_name(model_ui)
    if model_id is None:
        return _model_cards_html(), "Choose a specific model first."
    try:
        if engine.loaded_model_id == model_id:
            engine.unload()
        removed = model_manager.remove(model_id)
        note = f"Removed **{model_ui_name(model_id)}** from this computer." if removed else f"**{model_ui_name(model_id)}** was not installed."
        return _model_cards_html(), note
    except Exception as exc:
        return _model_cards_html(), f"❌ {_friendly_error(exc)}"


def load_model_ui(model_ui, compute_preference, offline_mode, auto_download_models, progress=gr.Progress()):
    model_id = model_id_from_ui_name(model_ui)
    if model_id is None:
        return _model_cards_html(), "Choose a specific model first."
    try:
        device_label = _configure_engine(model_id, compute_preference, offline_mode, auto_download_models, progress)
        engine.load_model(model_id, progress_callback=_engine_progress(progress))
        progress(None)
        return _model_cards_html(), f"✅ **{model_ui_name(model_id)}** loaded on **{device_label}**."
    except Exception as exc:
        progress(None)
        return _model_cards_html(), f"❌ {_friendly_error(exc)}"


def unload_model_ui():
    engine.unload()
    return _model_cards_html(), "Model unloaded. Memory is free."


initial_project = settings.get("project") if settings.get("project") in _project_choices() else None
initial_project_payload = projects.load(initial_project) if initial_project else None
initial_script = initial_project_payload.get("script", SAMPLE_SCRIPT) if initial_project_payload else SAMPLE_SCRIPT
initial_voice = settings.get("voice") if settings.get("voice") in _voice_choices() else (_voice_choices()[0] if _voice_choices() else None)
initial_model = settings.get("model_selection") if settings.get("model_selection") in MODEL_SELECTIONS else AUTO_MODEL
initial_language = settings.get("language_selection") if settings.get("language_selection") in LANGUAGE_CHOICES else "Auto"

with gr.Blocks(title="Creator Studio", analytics_enabled=False) as demo:
    gr.HTML("<div class='app-hero'><div class='eyebrow'>LOCAL CREATOR TOOL</div><h1>Creator Studio</h1><p>Create voiceovers without carrying model internals through every step. Choose a voice, write, generate — and go deeper only when you need to.</p></div>")
    with gr.Tabs():
        with gr.Tab("Create"):
            with gr.Row(equal_height=False):
                with gr.Column(scale=2, min_width=440):
                    project_dropdown = gr.Dropdown(label="Project", choices=_project_choices(), value=initial_project)
                    script = gr.Textbox(label="Script", lines=14, value=initial_script, placeholder="Write or paste what you want to hear…")
                    with gr.Row():
                        pause_025 = gr.Button("Pause 0.25s", size="sm")
                        pause_05 = gr.Button("Pause 0.5s", size="sm")
                        pause_1 = gr.Button("Pause 1s", size="sm")
                        pause_2 = gr.Button("Pause 2s", size="sm")
                    generate_btn = gr.Button("Generate", variant="primary")
                    compare_btn = gr.Button("Compare models", variant="secondary")
                    create_status = gr.Markdown("Ready when you are.", elem_classes="status-line")
                    output_audio = gr.Audio(label="Result", interactive=False, format="wav")
                    output_download = gr.DownloadButton("Download audio")
                    with gr.Accordion("Details", open=False):
                        generation_details = gr.Markdown("Generation details appear here.")
                        metadata_download = gr.DownloadButton("Download generation details")
                        project_takes = gr.Dropdown(label="Project takes", choices=[])
                        with gr.Row():
                            takes_refresh = gr.Button("Refresh takes")
                            take_load = gr.Button("Load take")
                    with gr.Accordion("Compare results", open=False):
                        compare_status = gr.Markdown("One click compares every compatible model. Models are unloaded between runs when that is safer for memory.")
                        with gr.Column():
                            gr.Markdown("#### Multilingual")
                            compare_v3_audio = gr.Audio(interactive=False)
                            compare_v3_download = gr.DownloadButton("Download")
                            gr.Markdown("#### Expressive")
                            compare_turbo_audio = gr.Audio(interactive=False)
                            compare_turbo_download = gr.DownloadButton("Download")
                            gr.Markdown("#### Light")
                            compare_nano_audio = gr.Audio(interactive=False)
                            compare_nano_download = gr.DownloadButton("Download")
                with gr.Column(scale=1, min_width=320):
                    gr.Markdown("### Voice")
                    voice_dropdown = gr.Dropdown(label="Voice", choices=_voice_choices(), value=initial_voice)
                    with gr.Accordion("+ Add a voice", open=not bool(_voice_choices())):
                        quick_voice_audio = gr.Audio(label="Drop a recording or record now", sources=["upload", "microphone"], type="filepath", format="wav")
                        quick_voice_name = gr.Textbox(label="Name", placeholder="My voice")
                        quick_voice_save = gr.Button("Save & use", variant="primary")
                        quick_voice_status = gr.Markdown("A clean 8–15 second recording usually works well.")
                    gr.Markdown("### Sound")
                    model_ui = gr.Dropdown(label="Model", choices=list(MODEL_SELECTIONS), value=initial_model, info="Auto chooses a sensible model for the language and this computer.")
                    language_ui = gr.Dropdown(label="Language", choices=LANGUAGE_CHOICES, value=initial_language)
                    preset = gr.Dropdown(label="Style", choices=list(PRESETS), value=settings.get("preset", "Creator"))
                    speech_speed = gr.Slider(0.75, 1.25, value=float(settings["speech_speed"]), step=0.01, label="Speed")
                    model_state = gr.Markdown(_model_state_line(initial_model, initial_language, initial_script, settings.get("compute_preference", "Auto")), elem_classes="model-choice-note")
                    gr.Markdown("<div class='soft-note'>Need more control? Everything technical lives in <b>Settings → Expert</b>, not in your way here.</div>")

        with gr.Tab("Library"):
            with gr.Tabs():
                with gr.Tab("Voices"):
                    with gr.Row(equal_height=False):
                        with gr.Column(scale=1):
                            library_voice_audio = gr.Audio(label="Add a voice", sources=["upload", "microphone"], type="filepath", format="wav")
                            library_voice_name = gr.Textbox(label="Name", placeholder="Narrator")
                            library_voice_save = gr.Button("Save voice", variant="primary")
                            library_voice_refresh = gr.Button("Refresh")
                        with gr.Column(scale=2):
                            voice_manage = gr.Dropdown(label="Saved voices", choices=_voice_choices(), value=initial_voice)
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
                    project_name = gr.Textbox(label="Project name", placeholder="Video 01")
                    create_project_btn = gr.Button("Create project", variant="primary")
                    load_project_btn = gr.Button("Open selected project")
                    save_project_btn = gr.Button("Save now")
                    delete_project_btn = gr.Button("Delete project", variant="stop")
                    project_status = gr.Markdown("Projects keep scripts, choices, and takes together.")

                with gr.Tab("History"):
                    history = gr.Dropdown(label="Recent generations", choices=engine.recent_outputs())
                    history_refresh = gr.Button("Refresh")
                    history_load = gr.Button("Open")
                    history_delete = gr.Button("Delete", variant="stop")
                    history_audio = gr.Audio(label="Preview", interactive=False)
                    history_download = gr.DownloadButton("Download audio")
                    history_metadata = gr.DownloadButton("Download details")
                    history_note = gr.Markdown()

        with gr.Tab("Tools"):
            with gr.Tabs():
                with gr.Tab("Transcribe"):
                    stt_audio = gr.Audio(label="Audio", sources=["upload", "microphone"], type="filepath")
                    stt_quality = gr.Radio(label="Quality", choices=list(QUALITY_MODES), value="Balanced")
                    stt_language = gr.Dropdown(label="Language", choices=LANGUAGE_CHOICES, value="Auto")
                    stt_btn = gr.Button("Transcribe", variant="primary")
                    stt_text = gr.Textbox(label="Transcript", lines=12)
                    stt_segments = gr.Dataframe(headers=["start", "end", "text"], datatype=["number", "number", "str"], interactive=False, wrap=True)
                    stt_status = gr.Markdown(speech_tools_status())

                with gr.Tab("Batch & subtitles"):
                    batch_state = gr.State([])
                    batch_file = gr.File(label="TXT / MD / CSV / JSON / SRT / VTT", type="filepath")
                    batch_parse = gr.Button("Read file")
                    batch_table = gr.Dataframe(headers=["id", "text", "start", "end", "target duration"], datatype=["str", "str", "number", "number", "number"], interactive=False, wrap=True)
                    batch_voice = gr.Dropdown(label="Voice", choices=_voice_choices(), value=initial_voice)
                    batch_model = gr.Dropdown(label="Model", choices=list(MODEL_SELECTIONS), value=initial_model)
                    batch_language = gr.Dropdown(label="Language", choices=LANGUAGE_CHOICES, value=initial_language)
                    batch_fit = gr.Checkbox(label="Fit subtitle clips to cue timing", value=bool(settings["batch_fit_timing"]))
                    batch_stretch = gr.Slider(1.02, 1.5, value=float(settings["batch_max_stretch"]), step=0.01, label="Maximum safe timing adjustment")
                    batch_run = gr.Button("Generate batch", variant="primary")
                    batch_manifest = gr.DownloadButton("Download manifest")
                    batch_output_dir = gr.Textbox(label="Output folder", interactive=False)
                    batch_status = gr.Markdown()

        with gr.Tab("Models"):
            gr.Markdown("## Models")
            gr.Markdown("Installed snapshots stay pinned until you deliberately update them. Deleting a speech model never deletes your voices or projects.")
            models_html = gr.HTML(_model_cards_html(), elem_id="model-cards")
            model_action = gr.Dropdown(label="Model", choices=[MODEL_UI_NAMES[mid] for mid in MODEL_SPECS], value=MODEL_UI_NAMES["nano"])
            with gr.Row():
                model_install = gr.Button("Install")
                model_check = gr.Button("Check update")
                model_update = gr.Button("Update")
                model_remove = gr.Button("Remove", variant="stop")
            with gr.Row():
                model_load = gr.Button("Load now")
                model_unload = gr.Button("Unload current")
                model_refresh = gr.Button("Refresh")
            model_action_status = gr.Markdown("Choose a model and an action.")

        with gr.Tab("Settings"):
            with gr.Row(equal_height=False):
                with gr.Column(scale=1):
                    gr.Markdown("## General")
                    compute_preference = gr.Radio(label="Performance", choices=list(COMPUTE_CHOICES), value=settings.get("compute_preference", "Auto"), info="Auto is recommended. It uses GPU acceleration when the installed runtime can use it and otherwise stays on CPU.")
                    quality_mode = gr.Radio(label="Generation quality", choices=list(QUALITY_MODES), value=settings.get("generation_quality", "Balanced"), info="Fast favors speed. Balanced adds a lightweight quality check. Best can retry and compare candidates.")
                    offline_mode = gr.Checkbox(label="Offline mode", value=bool(settings.get("offline_mode", False)), info="Blocks missing-model downloads and update checks while enabled.")
                    auto_download_models = gr.Checkbox(label="Download a missing selected model automatically", value=bool(settings.get("auto_download_models", True)), info="Turn this off if every model download should be an explicit action in Models.")
                    gr.Markdown("### Audio finishing")
                    trim_silence = gr.Checkbox(label="Trim empty silence at the ends", value=bool(settings["trim_silence"]))
                    peak_normalize = gr.Checkbox(label="Normalize output level", value=bool(settings["peak_normalize"]))
                    fade_ms = gr.Slider(0, 250, value=int(settings["fade_ms"]), step=5, label="Short fade (ms)")
                    save_preferences = gr.Button("Save settings", variant="primary")
                    preferences_status = gr.Markdown()

                    gr.Markdown("### Speech tools")
                    speech_tools_note = gr.Markdown(speech_tools_status())
                    install_speech_btn = gr.Button("Install speech tools")

                with gr.Column(scale=2):
                    with gr.Accordion("Expert", open=False):
                        gr.Markdown("These controls are optional. Normal generation does not require understanding them.")
                        raw_mode = gr.Checkbox(label="Raw model text path", value=bool(settings["raw_mode"]), info="Bypasses pause parsing, chunking, and text cleanup.")
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
                        quality_check = gr.Checkbox(label="Check generated audio automatically", value=bool(settings["quality_check"]))
                        auto_retries = gr.Slider(0, 3, value=int(settings["auto_retries"]), step=1, label="Automatic retries")
                        best_of_n = gr.Slider(1, 3, value=int(settings["best_of_n"]), step=1, label="Generate candidates and keep the best")
                        verify_stt = gr.Checkbox(label="Verify generated speech with local STT", value=bool(settings["verify_stt"]))
                        whisper_model = gr.Dropdown(label="STT verifier size", choices=["tiny", "base", "small"], value=settings["whisper_model"])
                        verification_threshold = gr.Slider(0.5, 0.98, value=float(settings["verification_threshold"]), step=0.01, label="Transcript similarity threshold")

                    with gr.Accordion("Diagnostics", open=False):
                        diagnostics_box = gr.Code(value=format_diagnostics(_diagnostics_snapshot()), language="json", label="Technical diagnostics", interactive=False)
                        diagnostics_refresh = gr.Button("Refresh diagnostics")

                    with gr.Accordion("About & open-source credits", open=False):
                        gr.Markdown(
                            """
                            Creator Studio is an independent local application. The current speech models are open-source Chatterbox models. Full attribution, source notices, licenses, and the independence notice are kept in `NOTICE.md`, `LICENSE`, and the repository README.
                            """
                        )

    gr.HTML("<div class='footer-note'><span>Runs locally by default.</span><span>Current speech models are open-source Chatterbox models; credits and licenses are in About.</span></div>")

    preset.change(apply_preset, inputs=preset, outputs=[exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, speech_speed], queue=False)
    quality_mode.change(apply_quality_mode, inputs=quality_mode, outputs=[quality_check, auto_retries, best_of_n], queue=False)
    for component in (model_ui, language_ui, script, compute_preference):
        component.change(_model_state_line, inputs=[model_ui, language_ui, script, compute_preference], outputs=model_state, queue=False)

    pause_025.click(lambda text: f"{text} [pause=0.25]".strip(), inputs=script, outputs=script, queue=False)
    pause_05.click(lambda text: f"{text} [pause=0.5]".strip(), inputs=script, outputs=script, queue=False)
    pause_1.click(lambda text: f"{text} [pause=1]".strip(), inputs=script, outputs=script, queue=False)
    pause_2.click(lambda text: f"{text} [pause=2]".strip(), inputs=script, outputs=script, queue=False)

    voice_outputs = [voice_dropdown, voice_manage, batch_voice, quick_voice_audio, quick_voice_status]
    quick_voice_save.click(add_voice, inputs=[quick_voice_audio, quick_voice_name], outputs=voice_outputs)
    library_voice_save.click(add_voice, inputs=[library_voice_audio, library_voice_name], outputs=[voice_dropdown, voice_manage, batch_voice, library_voice_audio, voice_manage_status])
    library_voice_refresh.click(refresh_voices, outputs=[voice_dropdown, voice_manage, batch_voice])
    voice_manage.change(inspect_voice, inputs=voice_manage, outputs=[voice_inspection, voice_preview])
    voice_inspect.click(inspect_voice, inputs=voice_manage, outputs=[voice_inspection, voice_preview])
    rename_voice_btn.click(rename_voice, inputs=[voice_manage, rename_voice_name], outputs=[voice_manage, voice_manage_status]).then(sync_voice_selection, inputs=voice_manage, outputs=[voice_dropdown, voice_manage, batch_voice])
    duplicate_voice_btn.click(duplicate_voice, inputs=voice_manage, outputs=[voice_manage, voice_manage_status]).then(sync_voice_selection, inputs=voice_manage, outputs=[voice_dropdown, voice_manage, batch_voice])
    delete_voice_btn.click(delete_voice, inputs=voice_manage, outputs=[voice_manage, voice_manage_status]).then(sync_voice_selection, inputs=voice_manage, outputs=[voice_dropdown, voice_manage, batch_voice])

    preprocess_inputs = [script, language_ui, raw_mode, normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls, collapse_punctuation, normalize_whitespace]
    preview_btn.click(preview_processed_text, inputs=preprocess_inputs, outputs=[preview_text, preview_note])

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
    compare_btn.click(
        compare_models,
        inputs=[script, project_dropdown, voice_dropdown, language_ui, speech_speed, compute_preference, quality_mode, offline_mode, auto_download_models,
                exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, top_k,
                raw_mode, smart_chunking, max_chars, chunk_gap, seed,
                normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls, collapse_punctuation, normalize_whitespace,
                quality_check, verify_stt, whisper_model, verification_threshold, auto_retries, best_of_n,
                trim_silence, peak_normalize, fade_ms],
        outputs=[compare_v3_audio, compare_v3_download, compare_turbo_audio, compare_turbo_download, compare_nano_audio, compare_nano_download, compare_status],
        show_progress="minimal",
    )

    quick_voice_save.click(sync_voice_selection, inputs=voice_dropdown, outputs=[voice_dropdown, voice_manage, batch_voice])
    project_refresh = gr.Button(visible=False)
    project_refresh.click(refresh_projects, outputs=project_dropdown)
    create_project_btn.click(create_project, inputs=project_name, outputs=[project_dropdown, project_status])
    load_project_btn.click(load_project, inputs=project_dropdown, outputs=[script, voice_dropdown, model_ui, language_ui, project_name, project_status])
    save_project_btn.click(save_project, inputs=[project_dropdown, project_name, script, voice_dropdown, model_ui, language_ui, compute_preference], outputs=project_status)
    delete_project_btn.click(delete_project, inputs=project_dropdown, outputs=[project_dropdown, project_status])
    takes_refresh.click(project_take_choices, inputs=project_dropdown, outputs=project_takes)
    take_load.click(load_take, inputs=project_takes, outputs=[output_audio, output_download])
    project_dropdown.change(project_take_choices, inputs=project_dropdown, outputs=project_takes)

    for component in (script, voice_dropdown, model_ui, language_ui):
        component.change(autosave_project, inputs=[project_dropdown, script, voice_dropdown, model_ui, language_ui, compute_preference], outputs=project_status, queue=False)

    history_refresh.click(refresh_history, outputs=history)
    history_load.click(load_history, inputs=history, outputs=[history_audio, history_download, history_metadata, history_note])
    history_delete.click(delete_history, inputs=history, outputs=[history, history_note])

    batch_parse.click(preview_batch_file, inputs=batch_file, outputs=[batch_state, batch_table, batch_status])
    batch_run.click(
        run_batch_ui,
        inputs=[batch_state, batch_voice, batch_model, batch_language, speech_speed, compute_preference, offline_mode, auto_download_models,
                exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, top_k,
                raw_mode, smart_chunking, max_chars, chunk_gap, seed,
                normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls, collapse_punctuation, normalize_whitespace,
                quality_check, verify_stt, whisper_model, verification_threshold, auto_retries, best_of_n,
                batch_fit, batch_stretch],
        outputs=[batch_manifest, batch_output_dir, batch_status],
        show_progress="minimal",
    )

    stt_btn.click(transcribe_ui, inputs=[stt_audio, stt_quality, stt_language, compute_preference], outputs=[stt_text, stt_segments, stt_status], show_progress="minimal")

    model_refresh.click(refresh_models, outputs=[models_html, model_action_status])
    model_install.click(model_download_ui, inputs=[model_action, offline_mode], outputs=[models_html, model_action_status], show_progress="minimal")
    model_check.click(check_model_update_ui, inputs=[model_action, offline_mode], outputs=[models_html, model_action_status], show_progress="minimal")
    model_update.click(update_model_ui, inputs=[model_action, offline_mode], outputs=[models_html, model_action_status], show_progress="minimal")
    model_remove.click(remove_model_ui, inputs=model_action, outputs=[models_html, model_action_status])
    model_load.click(load_model_ui, inputs=[model_action, compute_preference, offline_mode, auto_download_models], outputs=[models_html, model_action_status], show_progress="minimal")
    model_unload.click(unload_model_ui, outputs=[models_html, model_action_status])

    save_preferences.click(save_user_preferences, inputs=[compute_preference, quality_mode, offline_mode, auto_download_models, trim_silence, peak_normalize, fade_ms], outputs=preferences_status)
    install_speech_btn.click(install_speech_tools, outputs=speech_tools_note, show_progress="minimal")
    diagnostics_refresh.click(refresh_hardware, outputs=[hardware_box, diagnostics_box])


def launch() -> None:
    demo.queue(default_concurrency_limit=1).launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=False,
        inbrowser=False,
        css=CSS_PATH.read_text(encoding="utf-8") if CSS_PATH.exists() else "",
        show_error=True,
    )


if __name__ == "__main__":
    launch()
