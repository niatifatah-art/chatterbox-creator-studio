from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import gradio as gr

from studio.audio import AudioProcessOptions, process_audio
from studio.batch import BatchItem, load_batch, preview_rows
from studio.batch_runner import run_batch
from studio.diagnostics import collect_diagnostics, format_diagnostics
from studio.engine import ChatterboxEngine
from studio.model_cache import delete_cached_model, hf_hub_dir, list_chatterbox_cache
from studio.models import (
    DEFAULT_MODEL_ID,
    MODEL_SPECS,
    PARALINGUISTIC_TAGS,
    language_code_from_name,
    language_name_from_code,
    model_choices,
    model_id_from_name,
    model_name,
)
from studio.preprocess import PreprocessOptions, process_text
from studio.presets import PRESETS, preset_values
from studio.projects import ProjectStore
from studio.reliability import GenerationPolicy, generate_reliably
from studio.settings import SettingsStore
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
engine = ChatterboxEngine(OUTPUT_DIR)

SAMPLE_SCRIPT = """I wanted a cleaner way to make voiceovers, without repeating the same setup every time. [pause=0.45] So I built a local creator studio where I can save voices, keep projects, generate multiple takes, add exact pauses, run batches, and still switch back to raw Chatterbox whenever I want. [pause=0.65] The helpers are optional. The model is still yours to control."""


def append_token(text: str, token: str) -> str:
    text = text or ""
    if not text:
        return token
    spacer = "\n" if token.startswith("[pause=") else " "
    return f"{text.rstrip()}{spacer}{token}"


def make_insert_handler(token: str):
    def _insert(text: str) -> str:
        return append_token(text, token)

    return _insert


def apply_preset(name: str):
    return preset_values(name)


def _model_note(model_id: str) -> str:
    spec = MODEL_SPECS[model_id]
    caps = spec.capabilities
    badges = [f"{len(spec.languages)} languages" if caps.multilingual else "English"]
    if caps.supports_paralinguistic_tags:
        badges.append("native expression tags")
    if caps.cpu_friendly:
        badges.append("CPU-friendly")
    badges.append("local")
    return f"**{spec.name}** · {' · '.join(badges)}  \n{spec.description}"


def on_model_change(display_name: str, current_language: str):
    model_id = model_id_from_name(display_name)
    spec = MODEL_SPECS[model_id]
    language_choices = spec.language_choices()
    language_value = current_language if current_language in language_choices else language_choices[0]
    caps = spec.capabilities
    return (
        gr.update(choices=language_choices, value=language_value),
        _model_note(model_id),
        gr.update(visible=caps.supports_exaggeration),
        gr.update(visible=caps.supports_cfg),
        gr.update(visible=caps.supports_min_p),
        gr.update(visible=caps.supports_top_k),
        gr.update(visible=caps.supports_paralinguistic_tags),
    )


def mode_note(raw_mode: bool) -> str:
    if raw_mode:
        return (
            "⚠️ **Raw Chatterbox:** the script goes to the selected model without Studio pause parsing, "
            "smart chunking, or text preprocessing. Reliability checks can still be enabled separately."
        )
    return (
        "**Studio mode:** exact `[pause=…]` markers are inserted outside the model. "
        "Chunking, preprocessing, retries, verification and audio cleanup are individually optional."
    )


def _preprocess_options(raw_mode, normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls, collapse_punctuation, normalize_whitespace):
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


def _policy(raw_mode, normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls, collapse_punctuation, normalize_whitespace, quality_check, verify_stt, whisper_model, verification_threshold, auto_retries, best_of_n):
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


def _generation_kwargs(voice_name, model_display_name, language_name, exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, top_k, speech_speed, raw_mode, smart_chunking, max_chars, chunk_gap_seconds, seed):
    voice_path = voices.path_for(voice_name)
    if voice_path is None:
        raise ValueError("Add and select a saved voice profile first.")
    model_id = model_id_from_name(model_display_name)
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
    }


def preview_processed_text(script, language_name, raw_mode, normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls, collapse_punctuation, normalize_whitespace):
    language_id = language_code_from_name(language_name)
    options = _preprocess_options(
        raw_mode,
        normalize_unicode,
        normalize_punctuation,
        normalize_numbers,
        replace_urls,
        collapse_punctuation,
        normalize_whitespace,
    )
    result = process_text(script or "", language_id=language_id, options=options)
    if raw_mode:
        note = "Raw mode: no Studio text preprocessing will be applied."
    elif result.warnings:
        note = "⚠️ " + " ".join(result.warnings)
    else:
        note = "✅ Text preview is identical to the original." if not result.changed else "ℹ️ Preview shows the exact processed text that will be sent to the Studio pipeline."
    return result.processed, note


def _save_global_settings(values: dict):
    current = settings_store.load()
    current.update(values)
    settings_store.save(current)


def _project_choices() -> list[str]:
    return [item.id for item in projects.list()]


def create_project(name: str):
    try:
        project = projects.create(name or "Untitled Project")
    except Exception as exc:
        return gr.update(), f"❌ {exc}"
    choices = _project_choices()
    return gr.update(choices=choices, value=project["id"]), f"✅ Created **{project['name']}**."


def refresh_projects():
    choices = _project_choices()
    return gr.update(choices=choices, value=choices[0] if choices else None)


def load_project(project_id: str | None):
    project = projects.load(project_id)
    if project is None:
        return "", gr.update(), gr.update(), gr.update(), "", "No project selected."
    model_id = project.get("model_id") if project.get("model_id") in MODEL_SPECS else DEFAULT_MODEL_ID
    spec = MODEL_SPECS[model_id]
    language = language_name_from_code(project.get("language", "en"))
    if language not in spec.language_choices():
        language = spec.language_choices()[0]
    voice = project.get("voice") if project.get("voice") in voices.list() else None
    return (
        project.get("script", ""),
        gr.update(choices=voices.list(), value=voice),
        gr.update(value=spec.name),
        gr.update(choices=spec.language_choices(), value=language),
        project.get("name", project_id or ""),
        f"✅ Loaded **{project.get('name', project_id)}**.",
    )


def save_project(project_id, project_name, script, voice_name, model_display_name, language_name):
    if not project_id:
        return "Create or load a project first."
    try:
        model_id = model_id_from_name(model_display_name)
        language_id = language_code_from_name(language_name)
        project = projects.save(
            project_id,
            {
                "name": project_name or project_id,
                "script": script or "",
                "voice": voice_name,
                "model_id": model_id,
                "language": language_id,
                "settings": settings_store.load(),
            },
        )
        return f"✅ Saved **{project['name']}**."
    except Exception as exc:
        return f"❌ {exc}"


def delete_project(project_id: str | None):
    if not project_id:
        return gr.update(), "No project selected."
    if projects.delete(project_id):
        choices = _project_choices()
        return gr.update(choices=choices, value=choices[0] if choices else None), f"🗑️ Deleted project `{project_id}`."
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


def add_voice(audio_path: str | None, name: str | None):
    try:
        saved_name, _ = voices.save(audio_path, name)
        profile = voices.profile(saved_name)
    except Exception as exc:
        return gr.update(), f"❌ {exc}"
    choices = voices.list()
    warning = f"  \n⚠️ {profile.warning}" if profile and profile.warning else ""
    duration = f"{profile.duration_seconds:.1f}s" if profile and profile.duration_seconds is not None else "unknown duration"
    return gr.update(choices=choices, value=saved_name), f"✅ Saved **{saved_name}** · {duration}.{warning}"


def refresh_voices():
    choices = voices.list()
    value = choices[0] if choices else None
    return gr.update(choices=choices, value=value)


def inspect_voice(name: str | None):
    profile = voices.profile(name)
    if profile is None:
        return "No voice selected.", None
    bits = []
    if profile.duration_seconds is not None:
        bits.append(f"duration **{profile.duration_seconds:.2f}s**")
    if profile.sample_rate:
        bits.append(f"{profile.sample_rate} Hz")
    if profile.channels:
        bits.append(f"{profile.channels} channel(s)")
    if profile.silence_ratio is not None:
        bits.append(f"silence {profile.silence_ratio:.1%}")
    if profile.clipping_ratio is not None:
        bits.append(f"clipping {profile.clipping_ratio:.3%}")
    note = f"**{profile.name}** · " + " · ".join(bits)
    if profile.warning:
        note += f"  \n⚠️ {profile.warning}"
    return note, profile.path


def rename_voice(name: str | None, new_name: str | None):
    if not name:
        return gr.update(), "No voice selected."
    try:
        renamed = voices.rename(name, new_name or name)
        choices = voices.list()
        return gr.update(choices=choices, value=renamed), f"✅ Renamed voice to **{renamed}**."
    except Exception as exc:
        return gr.update(), f"❌ {exc}"


def duplicate_voice(name: str | None):
    if not name:
        return gr.update(), "No voice selected."
    try:
        duplicated = voices.duplicate(name)
        choices = voices.list()
        return gr.update(choices=choices, value=duplicated), f"✅ Duplicated as **{duplicated}**."
    except Exception as exc:
        return gr.update(), f"❌ {exc}"


def delete_voice(name: str | None):
    if not name:
        return gr.update(), "No voice selected."
    if voices.delete(name):
        choices = voices.list()
        return gr.update(choices=choices, value=choices[0] if choices else None), f"🗑️ Deleted **{name}**."
    return gr.update(), "Voice not found."


def generate_audio(
    script,
    project_id,
    voice_name,
    model_display_name,
    language_name,
    preset_name,
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
        raise gr.Error("Write some text first.")
    try:
        kwargs = _generation_kwargs(
            voice_name,
            model_display_name,
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
        )
        policy = _policy(
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
        )
        progress(0.05, desc=f"Preparing {model_name(kwargs['model_id'])}…")
        reliable = generate_reliably(engine, script, policy=policy, **kwargs)
        selected = reliable.selected
        final_audio = selected.result.audio_path
        if trim_silence or peak_normalize or int(fade_ms) > 0:
            processed_path = final_audio.with_name(f"{final_audio.stem}_final.wav")
            final_audio = process_audio(
                final_audio,
                processed_path,
                AudioProcessOptions(
                    trim_silence=bool(trim_silence),
                    peak_normalize=bool(peak_normalize),
                    fade_ms=int(fade_ms),
                ),
            )
        project_take_update = gr.update()
        if project_id:
            try:
                projects.add_take(project_id, final_audio, selected.result.metadata_path)
                project_take_update = project_take_choices(project_id)
            except Exception:
                pass

        setting_values = {
            "voice": voice_name,
            "project": project_id,
            "model_id": kwargs["model_id"],
            "language": kwargs["language_id"],
            "preset": preset_name,
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
        _save_global_settings(setting_values)
        progress(1.0, desc="Done")

        details = [
            f"✅ **{selected.result.model_name}**",
            f"**{engine.device_label}**",
            f"seed `{selected.result.seed}`",
            f"score `{selected.score:.3f}`",
            f"{len(reliable.candidates)} candidate(s)",
            f"{selected.result.chunk_count} chunk(s)",
        ]
        notes = []
        if reliable.processed_text.changed:
            notes.append("Text preprocessing changed the model input; inspect the preview/metadata if needed.")
        if reliable.processed_text.warnings:
            notes.extend(reliable.processed_text.warnings)
        if selected.quality and selected.quality.warnings:
            notes.extend(selected.quality.warnings)
        if selected.verification:
            if selected.verification.similarity is not None:
                details.append(f"STT `{selected.verification.similarity:.1%}`")
            if selected.verification.warning:
                notes.append(selected.verification.warning)
        status = " · ".join(details)
        if notes:
            status += "  \n⚠️ " + " ".join(dict.fromkeys(notes))

        return (
            str(final_audio),
            str(final_audio),
            str(selected.result.metadata_path),
            status,
            gr.update(choices=engine.recent_outputs(), value=final_audio.name),
            project_take_update,
        )
    except Exception as exc:
        raise gr.Error(str(exc)) from exc


def refresh_history():
    recent = engine.recent_outputs()
    return gr.update(choices=recent, value=recent[0] if recent else None)


def load_history(filename: str | None):
    path = engine.output_path(filename)
    metadata = engine.metadata_path(filename)
    if path is None:
        return None, None, None, "Generation not found."
    note = f"Loaded `{path.name}`."
    if metadata:
        try:
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            note += f"  \nModel: **{payload.get('model', {}).get('name', '?')}** · seed `{payload.get('seed', '?')}` · mode `{payload.get('mode', '?')}`."
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
        return gr.update(), f"❌ {exc}"
    recent = engine.recent_outputs()
    return gr.update(choices=recent, value=recent[0] if recent else None), f"🗑️ Deleted `{path.name}`."


def unload_model():
    previous = engine.loaded_model_id
    engine.unload()
    if previous:
        return f"✅ Unloaded **{model_name(previous)}** from memory."
    return "No Chatterbox model is currently loaded."


def preview_batch_file(file_path: str | None):
    if not file_path:
        return [], [], "Choose a TXT, MD, CSV, JSON, SRT or VTT file."
    try:
        items = load_batch(file_path)
    except Exception as exc:
        return [], [], f"❌ {exc}"
    state = [
        {
            "id": item.id,
            "text": item.text,
            "start_seconds": item.start_seconds,
            "end_seconds": item.end_seconds,
        }
        for item in items
    ]
    timed = sum(1 for item in items if item.target_duration_seconds is not None)
    return state, preview_rows(items), f"✅ Parsed **{len(items)}** item(s) · {timed} timed subtitle cue(s)."


def run_batch_ui(
    state,
    voice_name,
    model_display_name,
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
        raise gr.Error("Parse a batch file first.")
    items = [
        BatchItem(
            id=str(item.get("id")),
            text=str(item.get("text") or ""),
            start_seconds=item.get("start_seconds"),
            end_seconds=item.get("end_seconds"),
        )
        for item in state
    ]
    try:
        kwargs = _generation_kwargs(
            voice_name,
            model_display_name,
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
        )
        policy = _policy(
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
        )
        progress(0.05, desc=f"Generating {len(items)} batch item(s)…")
        summary = run_batch(
            engine,
            items,
            BATCH_DIR,
            generation_kwargs=kwargs,
            policy=policy,
            fit_to_timing=bool(fit_timing),
            max_duration_stretch=float(max_stretch),
        )
        progress(1.0, desc="Batch complete")
        return (
            str(summary.manifest_path),
            str(summary.output_dir),
            f"✅ Generated **{summary.generated}** · failed **{summary.failed}** · folder `{summary.output_dir}`.",
        )
    except Exception as exc:
        raise gr.Error(str(exc)) from exc


def refresh_diagnostics():
    return format_diagnostics(collect_diagnostics(ROOT))


def cache_rows():
    items = list_chatterbox_cache()
    rows = [[item.name, round(item.size_gb, 3), str(item.path)] for item in items]
    choices = [item.name for item in items]
    return rows, gr.update(choices=choices, value=choices[0] if choices else None), f"Hugging Face cache: `{hf_hub_dir()}`"


def delete_cache_item(name: str | None):
    if not name:
        return [], gr.update(), "No cached Chatterbox model selected."
    engine.unload()
    ok = delete_cached_model(name)
    rows, dropdown, note = cache_rows()
    return rows, dropdown, (f"🗑️ Deleted `{name}`. It will download again when needed.  \n" + note) if ok else "Could not delete that cache entry."


available_voices = voices.list()
initial_voice = settings.get("voice") if settings.get("voice") in available_voices else (available_voices[0] if available_voices else None)
project_ids = _project_choices()
initial_project = settings.get("project") if settings.get("project") in project_ids else (project_ids[0] if project_ids else None)
initial_model_id = settings.get("model_id") if settings.get("model_id") in MODEL_SPECS else DEFAULT_MODEL_ID
initial_spec = MODEL_SPECS[initial_model_id]
initial_model_name = initial_spec.name
initial_language_name = language_name_from_code(settings.get("language", "en"))
if initial_language_name not in initial_spec.language_choices():
    initial_language_name = initial_spec.language_choices()[0]
initial_preset = settings.get("preset", "Creator") if settings.get("preset") in PRESETS else "Creator"
initial_caps = initial_spec.capabilities

with gr.Blocks(title="Chatterbox Creator Studio", analytics_enabled=False) as demo:
    gr.HTML(
        """
        <div class="hero">
          <div class="eyebrow">LOCAL • OPEN SOURCE • CREATOR-FIRST</div>
          <h1>Chatterbox Creator Studio</h1>
          <p>A local production layer for Chatterbox V3, Turbo and Nano: projects, takes, voices, exact pauses, batch/subtitles, optional reliability checks and raw-model escape hatches.</p>
          <div class="model-strip">
            <span>V3 <small>multilingual</small></span>
            <span>Turbo <small>fast + expressive</small></span>
            <span>Nano <small>small + CPU-friendly</small></span>
            <span>Local <small>no account / no telemetry</small></span>
          </div>
        </div>
        """
    )

    with gr.Tabs():
        with gr.Tab("Studio"):
            with gr.Row(equal_height=False):
                with gr.Column(scale=7, elem_classes="main-panel"):
                    with gr.Row():
                        project_dropdown = gr.Dropdown(label="Project", choices=project_ids, value=initial_project, allow_custom_value=False)
                        project_refresh = gr.Button("↻", size="sm")
                    with gr.Row():
                        raw_mode = gr.Checkbox(label="Raw Chatterbox", value=bool(settings["raw_mode"]), info="No Studio text mutation/chunking/pause parsing.")
                        smart_chunking = gr.Checkbox(label="Smart chunking", value=bool(settings["smart_chunking"]), info="Language-aware long-text splitting.")
                    mode_status = gr.Markdown(mode_note(bool(settings["raw_mode"])), elem_classes="mode-note")
                    script = gr.Textbox(
                        label="Script",
                        value=SAMPLE_SCRIPT,
                        lines=16,
                        max_lines=30,
                        placeholder="Write your script. Use [pause=0.35] in Studio mode for exact silence.",
                        elem_id="script-box",
                    )
                    gr.Markdown("**Quick inserts**")
                    with gr.Row():
                        for label, token in [("+0.25s", "[pause=0.25]"), ("+0.5s", "[pause=0.5]"), ("+1s", "[pause=1]"), ("+2s", "[pause=2]")]:
                            button = gr.Button(label, size="sm", variant="secondary")
                            button.click(make_insert_handler(token), inputs=script, outputs=script, queue=False)
                    with gr.Column(visible=initial_caps.supports_paralinguistic_tags) as expression_tools:
                        gr.Markdown("**Turbo / Nano expression tags**")
                        with gr.Row():
                            for token in PARALINGUISTIC_TAGS:
                                button = gr.Button(token, size="sm", variant="secondary")
                                button.click(make_insert_handler(token), inputs=script, outputs=script, queue=False)
                    with gr.Accordion("Preview model input", open=False):
                        preview_text = gr.Textbox(label="Processed text preview", lines=7, interactive=False)
                        preview_note = gr.Markdown()
                        preview_btn = gr.Button("Preview processed text", size="sm")
                    generate_btn = gr.Button("Generate voice", variant="primary", size="lg", elem_id="generate-btn")
                    output_audio = gr.Audio(label="Selected result", interactive=False, format="wav")
                    with gr.Row():
                        output_file = gr.File(label="Download WAV", interactive=False)
                        metadata_file = gr.File(label="Generation metadata", interactive=False)
                    status = gr.Markdown(f"**Device:** {engine.device_label} · Helpers are opt-in; Raw mode remains available.")
                    with gr.Accordion("Project takes", open=False):
                        project_takes = gr.Dropdown(label="Takes", choices=[])
                        with gr.Row():
                            takes_refresh = gr.Button("Refresh takes", size="sm")
                            take_load = gr.Button("Load take", size="sm")
                with gr.Column(scale=4, elem_classes="side-panel"):
                    gr.Markdown("### Model & voice")
                    model = gr.Dropdown(label="Chatterbox model", choices=model_choices(), value=initial_model_name, allow_custom_value=False)
                    model_info = gr.Markdown(_model_note(initial_model_id), elem_classes="model-note")
                    language = gr.Dropdown(label="Language", choices=initial_spec.language_choices(), value=initial_language_name, allow_custom_value=False)
                    voice_dropdown = gr.Dropdown(label="Saved voice", choices=available_voices, value=initial_voice, allow_custom_value=False)
                    preset = gr.Dropdown(label="Preset", choices=list(PRESETS), value=initial_preset)
                    with gr.Accordion("Generation controls", open=False):
                        exaggeration = gr.Slider(0.0, 1.5, value=float(settings["exaggeration"]), step=0.05, label="Exaggeration", visible=initial_caps.supports_exaggeration)
                        cfg_weight = gr.Slider(0.0, 1.0, value=float(settings["cfg_weight"]), step=0.05, label="CFG / pacing", visible=initial_caps.supports_cfg)
                        temperature = gr.Slider(0.05, 1.5, value=float(settings["temperature"]), step=0.05, label="Temperature")
                        repetition_penalty = gr.Slider(1.0, 2.0, value=float(settings["repetition_penalty"]), step=0.05, label="Repetition penalty")
                        min_p = gr.Slider(0.0, 0.5, value=float(settings["min_p"]), step=0.01, label="Min P", visible=initial_caps.supports_min_p)
                        top_p = gr.Slider(0.1, 1.0, value=float(settings["top_p"]), step=0.01, label="Top P")
                        top_k = gr.Slider(50, 2000, value=int(settings["top_k"]), step=50, label="Top K", visible=initial_caps.supports_top_k)
                        seed = gr.Number(value=int(settings["seed"]), precision=0, label="Seed", info="-1 = random. The actual seed is saved.")
                        speech_speed = gr.Slider(0.75, 1.25, value=float(settings["speech_speed"]), step=0.01, label="Post speech speed")
                    with gr.Accordion("Long-form", open=False):
                        max_chars = gr.Slider(80, 500, value=int(settings["max_chars"]), step=10, label="Target max chars / chunk")
                        chunk_gap = gr.Slider(0.0, 1.0, value=float(settings["chunk_gap_seconds"]), step=0.01, label="Digital chunk gap (seconds)")
                    unload_btn = gr.Button("Unload current model", size="sm")
                    unload_status = gr.Markdown()

        with gr.Tab("Projects"):
            with gr.Row():
                with gr.Column(scale=2):
                    project_name = gr.Textbox(label="Project name", placeholder="YouTube video 01")
                    create_project_btn = gr.Button("Create project", variant="primary")
                    load_project_btn = gr.Button("Load selected project")
                    save_project_btn = gr.Button("Save current Studio state")
                    delete_project_btn = gr.Button("Delete selected project", variant="stop")
                    project_status = gr.Markdown("Projects live in `data/projects/` and contain portable JSON plus copied takes.")
                with gr.Column(scale=3):
                    gr.Markdown("### Workflow")
                    gr.Markdown(
                        "A project stores the script, selected voice/model/language and a settings snapshot. Every Studio generation can also be copied into the project as a take, so you can compare versions without overwriting previous WAVs."
                    )

        with gr.Tab("Voices"):
            with gr.Row():
                with gr.Column(scale=2):
                    voice_audio = gr.Audio(label="Reference voice", sources=["upload", "microphone"], type="filepath", format="wav")
                    voice_name = gr.Textbox(label="Voice name", placeholder="My Voice")
                    save_voice_btn = gr.Button("Save voice", variant="primary")
                    refresh_voice_btn = gr.Button("Refresh voices")
                with gr.Column(scale=3):
                    voice_manage = gr.Dropdown(label="Manage saved voice", choices=available_voices, value=initial_voice)
                    voice_preview = gr.Audio(label="Saved reference", interactive=False)
                    voice_inspection = gr.Markdown("Select a voice and inspect it. Analysis is advisory and never blocks generation.")
                    inspect_voice_btn = gr.Button("Inspect")
                    rename_voice_name = gr.Textbox(label="New name")
                    with gr.Row():
                        rename_voice_btn = gr.Button("Rename")
                        duplicate_voice_btn = gr.Button("Duplicate")
                        delete_voice_btn = gr.Button("Delete", variant="stop")
                    voice_manage_status = gr.Markdown()

        with gr.Tab("Batch / Subtitles"):
            batch_state = gr.State([])
            with gr.Row():
                with gr.Column(scale=2):
                    batch_file = gr.File(label="TXT / MD / CSV / JSON / SRT / VTT", type="filepath")
                    batch_parse_btn = gr.Button("Parse batch", variant="secondary")
                    batch_fit_timing = gr.Checkbox(label="Fit SRT/VTT output to cue duration", value=bool(settings["batch_fit_timing"]))
                    batch_max_stretch = gr.Slider(1.02, 1.5, value=float(settings["batch_max_stretch"]), step=0.01, label="Maximum safe time-stretch")
                    batch_run_btn = gr.Button("Generate batch", variant="primary")
                    batch_manifest = gr.File(label="Batch manifest", interactive=False)
                    batch_output_dir = gr.Textbox(label="Output folder", interactive=False)
                    batch_status = gr.Markdown()
                with gr.Column(scale=4):
                    batch_table = gr.Dataframe(
                        headers=["id", "text", "start", "end", "target duration"],
                        datatype=["str", "str", "number", "number", "number"],
                        interactive=False,
                        wrap=True,
                        label="Parsed items",
                    )

        with gr.Tab("History"):
            with gr.Row():
                with gr.Column(scale=2):
                    history = gr.Dropdown(label="Recent WAVs", choices=engine.recent_outputs())
                    history_refresh = gr.Button("Refresh")
                    history_load = gr.Button("Load")
                    history_delete = gr.Button("Delete", variant="stop")
                    history_note = gr.Markdown()
                with gr.Column(scale=3):
                    history_audio = gr.Audio(label="Preview", interactive=False)
                    history_file = gr.File(label="WAV", interactive=False)
                    history_metadata = gr.File(label="Metadata", interactive=False)

        with gr.Tab("Options"):
            gr.Markdown("All helpers below are optional. Raw Chatterbox turns off Studio text preprocessing automatically.")
            with gr.Row():
                with gr.Column():
                    gr.Markdown("### Text preprocessing")
                    normalize_unicode = gr.Checkbox(label="Unicode NFC", value=bool(settings["normalize_unicode"]))
                    normalize_punctuation = gr.Checkbox(label="Normalize punctuation", value=bool(settings["normalize_punctuation"]))
                    normalize_numbers = gr.Checkbox(label="Numbers → words (optional num2words)", value=bool(settings["normalize_numbers"]))
                    replace_urls = gr.Checkbox(label="Replace URLs with 'URL'", value=bool(settings["replace_urls"]))
                    collapse_punctuation = gr.Checkbox(label="Collapse extreme repeated punctuation", value=bool(settings["collapse_repeated_punctuation"]))
                    normalize_whitespace = gr.Checkbox(label="Normalize spaces", value=bool(settings["normalize_whitespace"]))
                with gr.Column():
                    gr.Markdown("### Reliability")
                    quality_check = gr.Checkbox(label="Local audio quality checks", value=bool(settings["quality_check"]))
                    verify_stt = gr.Checkbox(label="Verify with Faster-Whisper", value=bool(settings["verify_stt"]), info="Optional dependency; CPU verification can be slow.")
                    whisper_model = gr.Dropdown(label="Whisper verifier", choices=["tiny", "base", "small"], value=str(settings["whisper_model"]))
                    verification_threshold = gr.Slider(0.5, 0.98, value=float(settings["verification_threshold"]), step=0.01, label="STT similarity threshold")
                    auto_retries = gr.Slider(0, 3, value=int(settings["auto_retries"]), step=1, label="Auto retries")
                    best_of_n = gr.Slider(1, 3, value=int(settings["best_of_n"]), step=1, label="Best of N", info="Generates N candidates and picks the highest local score.")
                with gr.Column():
                    gr.Markdown("### Post-processing")
                    trim_silence = gr.Checkbox(label="Trim leading/trailing silence", value=bool(settings["trim_silence"]))
                    peak_normalize = gr.Checkbox(label="Peak normalize", value=bool(settings["peak_normalize"]))
                    fade_ms = gr.Slider(0, 250, value=int(settings["fade_ms"]), step=5, label="Fade in/out (ms)")
                    gr.Markdown("Post-processing is **off by default** and never changes the saved upstream model behavior in Raw mode unless you explicitly enable it.")

        with gr.Tab("Diagnostics / Models"):
            with gr.Row():
                with gr.Column(scale=3):
                    diagnostics_box = gr.Code(value=refresh_diagnostics(), language="json", label="Diagnostics", interactive=False)
                    diagnostics_refresh = gr.Button("Refresh diagnostics")
                with gr.Column(scale=3):
                    cache_table = gr.Dataframe(headers=["cache entry", "GB", "path"], interactive=False, label="Chatterbox Hugging Face cache")
                    cache_dropdown = gr.Dropdown(label="Cache entry")
                    cache_note = gr.Markdown()
                    cache_refresh = gr.Button("Refresh model cache")
                    cache_delete = gr.Button("Delete selected cached model", variant="stop")
                    gr.Markdown("Deleting cache does **not** delete voices/projects. The model will simply download again next time.")

    raw_mode.change(mode_note, inputs=raw_mode, outputs=mode_status, queue=False)
    model.change(
        on_model_change,
        inputs=[model, language],
        outputs=[language, model_info, exaggeration, cfg_weight, min_p, top_k, expression_tools],
        queue=False,
    )
    preset.change(
        apply_preset,
        inputs=preset,
        outputs=[exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, speech_speed],
        queue=False,
    )

    preprocess_inputs = [script, language, raw_mode, normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls, collapse_punctuation, normalize_whitespace]
    preview_btn.click(preview_processed_text, inputs=preprocess_inputs, outputs=[preview_text, preview_note], queue=False)

    generation_inputs = [
        script,
        project_dropdown,
        voice_dropdown,
        model,
        language,
        preset,
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
        chunk_gap,
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
    ]
    generate_btn.click(
        generate_audio,
        inputs=generation_inputs,
        outputs=[output_audio, output_file, metadata_file, status, history, project_takes],
    )

    project_refresh.click(refresh_projects, outputs=project_dropdown, queue=False)
    create_project_btn.click(create_project, inputs=project_name, outputs=[project_dropdown, project_status], queue=False)
    load_project_btn.click(load_project, inputs=project_dropdown, outputs=[script, voice_dropdown, model, language, project_name, project_status], queue=False)
    save_project_btn.click(save_project, inputs=[project_dropdown, project_name, script, voice_dropdown, model, language], outputs=project_status, queue=False)
    delete_project_btn.click(delete_project, inputs=project_dropdown, outputs=[project_dropdown, project_status], queue=False)
    takes_refresh.click(project_take_choices, inputs=project_dropdown, outputs=project_takes, queue=False)
    take_load.click(load_take, inputs=project_takes, outputs=[output_audio, output_file], queue=False)

    save_voice_btn.click(add_voice, inputs=[voice_audio, voice_name], outputs=[voice_dropdown, voice_manage_status], queue=False)
    refresh_voice_btn.click(refresh_voices, outputs=voice_dropdown, queue=False)
    inspect_voice_btn.click(inspect_voice, inputs=voice_manage, outputs=[voice_inspection, voice_preview], queue=False)
    rename_voice_btn.click(rename_voice, inputs=[voice_manage, rename_voice_name], outputs=[voice_manage, voice_manage_status], queue=False)
    duplicate_voice_btn.click(duplicate_voice, inputs=voice_manage, outputs=[voice_manage, voice_manage_status], queue=False)
    delete_voice_btn.click(delete_voice, inputs=voice_manage, outputs=[voice_manage, voice_manage_status], queue=False)
    voice_manage.change(inspect_voice, inputs=voice_manage, outputs=[voice_inspection, voice_preview], queue=False)

    batch_parse_btn.click(preview_batch_file, inputs=batch_file, outputs=[batch_state, batch_table, batch_status], queue=False)
    batch_generation_inputs = [
        batch_state,
        voice_dropdown,
        model,
        language,
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
        chunk_gap,
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
        batch_fit_timing,
        batch_max_stretch,
    ]
    batch_run_btn.click(run_batch_ui, inputs=batch_generation_inputs, outputs=[batch_manifest, batch_output_dir, batch_status])

    history_refresh.click(refresh_history, outputs=history, queue=False)
    history_load.click(load_history, inputs=history, outputs=[history_audio, history_file, history_metadata, history_note], queue=False)
    history_delete.click(delete_history, inputs=history, outputs=[history, history_note], queue=False)
    unload_btn.click(unload_model, outputs=unload_status, queue=False)

    diagnostics_refresh.click(refresh_diagnostics, outputs=diagnostics_box, queue=False)
    cache_refresh.click(cache_rows, outputs=[cache_table, cache_dropdown, cache_note], queue=False)
    cache_delete.click(delete_cache_item, inputs=cache_dropdown, outputs=[cache_table, cache_dropdown, cache_note], queue=False)
    demo.load(cache_rows, outputs=[cache_table, cache_dropdown, cache_note], queue=False)
    if initial_project:
        demo.load(project_take_choices, inputs=project_dropdown, outputs=project_takes, queue=False)


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
