from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import gradio as gr

# Keep the proven v1.1 generation/storage/model code intact while the product shell
# evolves independently. Importing app builds the legacy component tree but does not
# launch it; this module is the supported UI entry point for v1.1.1.
import app as core

ROOT = Path(__file__).resolve().parent
UI_CSS_PATH = ROOT / "assets" / "product_v111.css"

MODEL_IDS = tuple(core.MODEL_SPECS)
MODEL_NAMES = {model_id: core.model_ui_name(model_id) for model_id in MODEL_IDS}


def _installed(model_id: str) -> bool:
    try:
        return bool(core.model_manager.status(model_id).installed)
    except Exception:
        return False


def _compare_data(language_ui: str, script: str, current: list[str] | None = None):
    compatible = tuple(core.safe_compare_order(core.compatible_models(language_ui, script or "")))
    choices: list[tuple[str, str]] = []
    installed = []
    for model_id in compatible:
        ready = _installed(model_id)
        if ready:
            installed.append(model_id)
        choices.append((f"{MODEL_NAMES[model_id]} · {'Ready' if ready else 'Install first'}", model_id))

    if current is None:
        selected = installed.copy()
    else:
        selected = [model_id for model_id in current if model_id in compatible]

    language = core.resolve_language(language_ui, script or "")
    if len(compatible) < 2:
        note = f"**{language}** currently has one compatible speech model, so there is nothing useful to compare."
        enabled = False
    elif len(selected) < 2:
        note = "Choose at least **two ready models**. Nothing is downloaded just because you compare."
        enabled = False
    else:
        missing = [MODEL_NAMES[mid] for mid in selected if not _installed(mid)]
        if missing:
            note = "Install **" + ", ".join(missing) + "** first. Compare never starts a model download on its own."
            enabled = False
        else:
            mode = "one at a time"  # Stable engine path; safe on CPU and memory-constrained systems.
            note = f"**{len(selected)} models selected** · they will run {mode} and each result appears as soon as it is ready."
            enabled = True
    return choices, selected, note, enabled


def refresh_compare_picker(language_ui, script, current):
    choices, selected, note, enabled = _compare_data(language_ui, script, current or [])
    return gr.update(choices=choices, value=selected), note, gr.update(interactive=enabled)


def compare_selection_note(selected, language_ui, script):
    _, selected, note, enabled = _compare_data(language_ui, script, selected or [])
    return note, gr.update(interactive=enabled)


def _compare_outputs(results: dict[str, str | None], status: str):
    values = []
    for model_id in ("multilingual-v3", "turbo", "nano"):
        path = results.get(model_id)
        values.extend(
            [
                gr.update(value=path, visible=path is not None),
                gr.update(value=path, visible=path is not None),
            ]
        )
    values.append(status)
    return tuple(values)


def compare_selected_models(
    selected_models,
    script,
    project_id,
    voice_name,
    language_ui,
    speech_speed,
    compute_preference,
    quality_mode,
    offline_mode,
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
    """Compare only what the creator selected; never download implicitly."""
    if not (script or "").strip():
        raise gr.Error("Write something first.")

    compatible = set(core.compatible_models(language_ui, script))
    requested = core.safe_compare_order(selected_models or [])
    requested = tuple(model_id for model_id in requested if model_id in compatible)
    if len(requested) < 2:
        raise gr.Error("Choose at least two compatible models to compare.")

    missing = [MODEL_NAMES[mid] for mid in requested if not _installed(mid)]
    if missing:
        raise gr.Error("Install " + ", ".join(missing) + " from Models first. Compare never downloads models automatically.")

    language_name = core.resolve_language(language_ui, script)
    results: dict[str, str | None] = {model_id: None for model_id in MODEL_IDS}
    completed: list[str] = []
    total = len(requested)

    try:
        yield _compare_outputs(results, f"**Starting comparison** · {total} selected models")
        for index, model_id in enumerate(requested, 1):
            label = MODEL_NAMES[model_id]
            yield _compare_outputs(results, f"**Comparing {index}/{total}** · preparing **{label}**…")
            try:
                # Deliberately false: this workflow may only use already-installed models.
                core._configure_engine(model_id, compute_preference, offline_mode, False, progress)
                kwargs = core._generation_kwargs(
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
                    progress_callback=core._engine_progress(progress, prefix=f"{label} · "),
                )
                reliable = core.generate_reliably(
                    core.engine,
                    script,
                    policy=core._policy(
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
                final_audio = core._finish_audio(
                    reliable.selected.result.audio_path,
                    trim_silence,
                    peak_normalize,
                    fade_ms,
                )
                results[model_id] = str(final_audio)
                completed.append(label)
                if project_id:
                    try:
                        core.projects.add_take(
                            project_id,
                            final_audio,
                            reliable.selected.result.metadata_path,
                            segment_id=f"compare-{model_id}",
                        )
                    except Exception:
                        pass
                remaining = total - index
                status = f"✅ **{label} ready**"
                if remaining:
                    status += f" · {remaining} remaining"
                yield _compare_outputs(results, status)
            except Exception as exc:
                completed.append(f"{label} failed")
                yield _compare_outputs(results, f"⚠️ **{label}** could not finish: {core._friendly_error(exc)}")
            finally:
                core.engine.unload()

        progress(None)
        if any(results.values()):
            yield _compare_outputs(results, "✅ **Comparison ready** · " + " · ".join(completed))
        else:
            raise gr.Error("None of the selected models produced a result.")
    finally:
        progress(None)
        core.engine.unload()


def _compare_busy():
    return gr.update(value="Comparing…", interactive=False), gr.update(visible=True)


def _compare_idle(selected):
    return gr.update(value="Compare selected", interactive=len(selected or []) >= 2), gr.update(visible=False)


def _generate_busy():
    return gr.update(value="Generating…", interactive=False)


def _generate_idle():
    return gr.update(value="Generate", interactive=True)


def _friendly_download_callback(progress: gr.Progress, model_id: str):
    label = MODEL_NAMES[model_id]

    def callback(current: int, total: int | None, _upstream_desc: str) -> None:
        if total and total > 0:
            desc = f"Downloading {label} · {current}/{total} files"
            core._gr_progress(progress, desc, current, total, unit="file")
        else:
            core._gr_progress(progress, f"Preparing {label} download…", None, None, unit="file")

    return callback


def install_model_explicit(model_ui: str, offline_mode: bool, progress=gr.Progress()):
    model_id = core.model_id_from_ui_name(model_ui)
    if model_id is None:
        raise gr.Error("Choose a model to install.")
    if offline_mode:
        raise gr.Error("Turn off Offline mode before downloading a model.")
    try:
        status = core.model_manager.download(
            model_id,
            offline=False,
            progress=_friendly_download_callback(progress, model_id),
        )
        progress(None)
        return core._model_cards_html(), f"✅ **{MODEL_NAMES[model_id]}** is ready · {status.size_gb:.2f} GB."
    except Exception as exc:
        progress(None)
        raise gr.Error(core._friendly_error(exc)) from exc


def update_model_explicit(model_ui: str, offline_mode: bool, progress=gr.Progress()):
    model_id = core.model_id_from_ui_name(model_ui)
    if model_id is None:
        raise gr.Error("Choose a model first.")
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
        return core._model_cards_html(), f"✅ **{MODEL_NAMES[model_id]}** updated. Your old version was not replaced until you chose Update."
    except Exception as exc:
        progress(None)
        raise gr.Error(core._friendly_error(exc)) from exc


def save_preferences_explicit(compute_preference, quality_mode, offline_mode, trim_silence, peak_normalize, fade_ms):
    # Product UI never performs surprise model downloads. Keep the stored setting aligned.
    return core.save_user_preferences(
        compute_preference,
        quality_mode,
        offline_mode,
        False,
        trim_silence,
        peak_normalize,
        fade_ms,
    )


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


def _pause_js(token: str) -> str:
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
initial_preset = core.settings.get("preset") if core.settings.get("preset") in core.PRESETS else "Creator"
initial_quality = core.settings.get("generation_quality") if core.settings.get("generation_quality") in core.QUALITY_MODES else "Balanced"
initial_compute = core.settings.get("compute_preference") if core.settings.get("compute_preference") in core.COMPUTE_CHOICES else "Auto"
initial_quality_policy = core.quality_policy(initial_quality)
initial_script = _initial_script()
compare_choices, compare_selected, compare_note, compare_enabled = _compare_data(initial_language_ui, initial_script, None)
no_auto_download = gr.State(False)

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
                        project_refresh = gr.Button("↻", size="sm", scale=0, min_width=44, elem_id="project-refresh")

                    script = gr.Textbox(
                        label="Script",
                        value=initial_script,
                        lines=15,
                        max_lines=30,
                        placeholder="Write or paste what you want to hear…",
                        elem_id="script-box",
                    )
                    with gr.Row(elem_classes="quick-row"):
                        pause_025 = gr.Button("+ 0.25s pause", size="sm", variant="secondary")
                        pause_05 = gr.Button("+ 0.5s pause", size="sm", variant="secondary")
                        pause_1 = gr.Button("+ 1s pause", size="sm", variant="secondary")
                        pause_2 = gr.Button("+ 2s pause", size="sm", variant="secondary")

                    with gr.Row(elem_classes="primary-actions"):
                        generate_btn = gr.Button("Generate", variant="primary", size="lg", elem_id="generate-btn")
                        compare_btn = gr.Button("Compare selected", variant="secondary", size="lg", elem_id="compare-btn", interactive=compare_enabled)
                        compare_cancel = gr.Button("Stop", variant="secondary", size="lg", visible=False, elem_id="compare-cancel")

                    create_status = gr.Markdown("Ready when you are.", elem_classes="status-line", elem_id="create-status")
                    output_audio = gr.Audio(label="Result", interactive=False, format="wav", elem_classes="result-audio")
                    output_download = gr.DownloadButton("Download audio", variant="secondary")

                    with gr.Accordion("Details", open=False):
                        generation_details = gr.Markdown("Generation details appear here.")
                        metadata_download = gr.DownloadButton("Download generation details", variant="secondary")
                        with gr.Row():
                            project_takes = gr.Dropdown(label="Project takes", choices=[])
                            takes_refresh = gr.Button("Refresh", size="sm")
                            take_load = gr.Button("Load", size="sm")

                    gr.Markdown("### Compare")
                    compare_picker = gr.CheckboxGroup(
                        label="Models to compare",
                        choices=compare_choices,
                        value=compare_selected,
                        info="Choose only the models you want to hear. Missing models are never downloaded automatically.",
                        elem_id="compare-model-picker",
                        elem_classes="choice-pills",
                    )
                    compare_status = gr.Markdown(compare_note, elem_classes="status-line", elem_id="compare-status")
                    with gr.Row(elem_classes="compare-results-row"):
                        with gr.Column(elem_classes="compare-card"):
                            gr.Markdown("#### Multilingual")
                            compare_v3_audio = gr.Audio(interactive=False, visible=False, label="Multilingual")
                            compare_v3_download = gr.DownloadButton("Download", visible=False)
                        with gr.Column(elem_classes="compare-card"):
                            gr.Markdown("#### Expressive")
                            compare_turbo_audio = gr.Audio(interactive=False, visible=False, label="Expressive")
                            compare_turbo_download = gr.DownloadButton("Download", visible=False)
                        with gr.Column(elem_classes="compare-card"):
                            gr.Markdown("#### Light")
                            compare_nano_audio = gr.Audio(interactive=False, visible=False, label="Light")
                            compare_nano_download = gr.DownloadButton("Download", visible=False)

                with gr.Column(scale=4, elem_classes="control-card"):
                    gr.Markdown("### Voice")
                    voice_dropdown = gr.Dropdown(label="Voice", choices=available_voices, value=initial_voice, allow_custom_value=False, elem_id="voice-picker")
                    with gr.Accordion("+ Add a voice", open=not bool(available_voices)):
                        quick_voice_audio = gr.Audio(label="Drop a recording or record now", sources=["upload", "microphone"], type="filepath", format="wav")
                        quick_voice_name = gr.Textbox(label="Name", placeholder="My voice")
                        quick_voice_save = gr.Button("Save & use", variant="primary")
                        quick_voice_status = gr.Markdown("A clean 8–15 second recording usually works well.")

                    gr.Markdown("### Sound")
                    model_ui = gr.Dropdown(label="Model", choices=list(core.MODEL_SELECTIONS), value=initial_model_ui, allow_custom_value=False, info="Auto chooses a sensible model for the language and this computer.", elem_id="model-picker")
                    language_ui = gr.Dropdown(label="Language", choices=core.LANGUAGE_CHOICES, value=initial_language_ui, allow_custom_value=False, elem_id="language-picker")
                    preset = gr.Dropdown(label="Style", choices=list(core.PRESETS), value=initial_preset)
                    speech_speed = gr.Slider(0.75, 1.25, value=float(core.settings["speech_speed"]), step=0.01, label="Speed")
                    model_state = gr.Markdown(core._model_state_line(initial_model_ui, initial_language_ui, initial_script, initial_compute), elem_classes="model-choice-note", elem_id="model-state")
                    gr.Markdown("<div class='soft-note'>Technical controls stay in <b>Settings → Expert</b>. You do not need them for normal generation.</div>")

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
                            batch_language = gr.Dropdown(label="Language", choices=core.LANGUAGE_CHOICES, value=initial_language_ui)
                            batch_fit = gr.Checkbox(label="Fit subtitle clips to cue timing", value=bool(core.settings["batch_fit_timing"]))
                            batch_stretch = gr.Slider(1.02, 1.5, value=float(core.settings["batch_max_stretch"]), step=0.01, label="Maximum safe timing adjustment")
                            batch_run = gr.Button("Generate batch", variant="primary")
                            batch_manifest = gr.DownloadButton("Download manifest")
                            batch_output_dir = gr.Textbox(label="Output folder", interactive=False)
                            batch_status = gr.Markdown()
                        with gr.Column(scale=4, elem_classes="workspace-card"):
                            batch_table = gr.Dataframe(headers=["id", "text", "start", "end", "target duration"], datatype=["str", "str", "number", "number", "number"], interactive=False, wrap=True, label="Items")

        with gr.Tab("Models"):
            gr.Markdown("## Models")
            gr.Markdown("Models change only when **you** press Install, Update, or Remove.")
            models_html = gr.HTML(core._model_cards_html(), elem_id="model-cards")
            with gr.Row(equal_height=False):
                with gr.Column(scale=2, elem_classes="workspace-card"):
                    model_action = gr.Radio(
                        label="Selected model",
                        choices=[core.MODEL_UI_NAMES[mid] for mid in core.MODEL_SPECS],
                        value=core.MODEL_UI_NAMES["nano"],
                        elem_id="model-action-picker",
                        elem_classes="choice-pills",
                    )
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
                    model_action_status = gr.Markdown("Choose a model above, then an action.", elem_classes="status-line")
                with gr.Column(scale=3, elem_classes="workspace-card"):
                    gr.Markdown("### This computer")
                    hardware_box = gr.Markdown(core.hardware_summary(core.hardware), elem_classes="hardware-card")
                    gr.Markdown("**Performance: Auto** uses the fastest backend this installation can actually use. You can override it in Settings.")

        with gr.Tab("Settings"):
            with gr.Row(equal_height=False):
                with gr.Column(scale=2, elem_classes="workspace-card"):
                    gr.Markdown("## Simple defaults")
                    compute_preference = gr.Radio(label="Performance", choices=list(core.COMPUTE_CHOICES), value=initial_compute, info="Auto is recommended.", elem_classes="choice-pills")
                    quality_mode = gr.Radio(label="Generation quality", choices=list(core.QUALITY_MODES), value=initial_quality, elem_classes="choice-pills")
                    offline_mode = gr.Checkbox(label="Offline mode", value=bool(core.settings.get("offline_mode", False)), info="Blocks model downloads and update checks while enabled.")
                    gr.Markdown("<div class='soft-note'>Model downloads are always explicit in this interface. Use the <b>Models</b> page when you want to install or update one.</div>")
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
                        gr.Markdown("Optional controls for people who specifically want them.")
                        raw_mode = gr.Checkbox(label="Raw model text path", value=bool(core.settings["raw_mode"]), info="Bypasses pause parsing, chunking, and text cleanup.")
                        smart_chunking = gr.Checkbox(label="Split long text automatically", value=bool(core.settings["smart_chunking"]))
                        max_chars = gr.Slider(80, 500, value=int(core.settings["max_chars"]), step=10, label="Target characters per chunk")
                        chunk_gap = gr.Slider(0.0, 1.0, value=float(core.settings["chunk_gap_seconds"]), step=0.01, label="Gap between chunks (seconds)")
                        seed = gr.Number(value=int(core.settings["seed"]), precision=0, label="Seed", info="-1 creates a fresh seed and records it.")

                        gr.Markdown("#### Voice generation")
                        exaggeration = gr.Slider(0.0, 1.5, value=float(core.settings["exaggeration"]), step=0.05, label="Expression strength")
                        cfg_weight = gr.Slider(0.0, 1.0, value=float(core.settings["cfg_weight"]), step=0.05, label="Pacing guidance")
                        temperature = gr.Slider(0.05, 1.5, value=float(core.settings["temperature"]), step=0.05, label="Variation")
                        repetition_penalty = gr.Slider(1.0, 2.0, value=float(core.settings["repetition_penalty"]), step=0.05, label="Repetition control")
                        min_p = gr.Slider(0.0, 0.5, value=float(core.settings["min_p"]), step=0.01, label="Min P")
                        top_p = gr.Slider(0.1, 1.0, value=float(core.settings["top_p"]), step=0.01, label="Top P")
                        top_k = gr.Slider(50, 2000, value=int(core.settings["top_k"]), step=50, label="Top K")

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
                        gr.Markdown(
                            """
                            Creator Studio is an independent local application. Current speech models are open-source Chatterbox models. Full attribution, source notices, licenses, and the independence notice are in `NOTICE.md`, `LICENSE`, and the repository README.
                            """
                        )

    gr.HTML("<div class='footer-note'><span>Runs locally by default.</span><span>Credits & licenses are in About.</span></div>")

    # Human-facing presets drive the technical values silently.
    preset.change(core.apply_preset, inputs=preset, outputs=[exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, speech_speed], queue=False)
    quality_mode.change(core.apply_quality_mode, inputs=quality_mode, outputs=[quality_check, auto_retries, best_of_n], queue=False)

    for component in (model_ui, language_ui, script, compute_preference):
        component.change(core._model_state_line, inputs=[model_ui, language_ui, script, compute_preference], outputs=model_state, queue=False)

    # Pause actions edit at the caret in the browser instead of appending to the end.
    for button, token in ((pause_025, "[pause=0.25]"), (pause_05, "[pause=0.5]"), (pause_1, "[pause=1]"), (pause_2, "[pause=2]")):
        button.click(lambda text: text, inputs=script, outputs=script, js=_pause_js(token), queue=False)

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
        script, project_dropdown, voice_dropdown, model_ui, language_ui, preset, speech_speed,
        compute_preference, quality_mode, offline_mode, no_auto_download,
        exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, top_k,
        raw_mode, smart_chunking, max_chars, chunk_gap, seed,
        normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls, collapse_punctuation, normalize_whitespace,
        quality_check, verify_stt, whisper_model, verification_threshold, auto_retries, best_of_n,
        trim_silence, peak_normalize, fade_ms,
    ]
    generate_start = generate_btn.click(_generate_busy, outputs=generate_btn, queue=False)
    generate_event = generate_start.then(
        core.generate_audio,
        inputs=generation_inputs,
        outputs=[output_audio, output_download, metadata_download, create_status, generation_details, history, project_takes, model_state],
        show_progress="minimal",
    )
    generate_event.then(_generate_idle, outputs=generate_btn, queue=False)

    # Keep Compare choices in sync with language/model compatibility and installation state.
    for component in (language_ui, script):
        component.change(refresh_compare_picker, inputs=[language_ui, script, compare_picker], outputs=[compare_picker, compare_status, compare_btn], queue=False)
    compare_picker.change(compare_selection_note, inputs=[compare_picker, language_ui, script], outputs=[compare_status, compare_btn], queue=False)

    compare_inputs = [
        compare_picker, script, project_dropdown, voice_dropdown, language_ui, speech_speed,
        compute_preference, quality_mode, offline_mode,
        exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, top_k,
        raw_mode, smart_chunking, max_chars, chunk_gap, seed,
        normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls, collapse_punctuation, normalize_whitespace,
        quality_check, verify_stt, whisper_model, verification_threshold, auto_retries, best_of_n,
        trim_silence, peak_normalize, fade_ms,
    ]
    compare_start = compare_btn.click(_compare_busy, outputs=[compare_btn, compare_cancel], queue=False)
    compare_event = compare_start.then(
        compare_selected_models,
        inputs=compare_inputs,
        outputs=[compare_v3_audio, compare_v3_download, compare_turbo_audio, compare_turbo_download, compare_nano_audio, compare_nano_download, compare_status],
        show_progress="minimal",
    )
    compare_event.then(_compare_idle, inputs=compare_picker, outputs=[compare_btn, compare_cancel], queue=False)
    compare_cancel.click(
        lambda: "Stopping comparison…",
        outputs=compare_status,
        cancels=[compare_event],
        queue=False,
    ).then(_compare_idle, inputs=compare_picker, outputs=[compare_btn, compare_cancel], queue=False)

    project_refresh.click(core.refresh_projects, outputs=project_dropdown, queue=False)
    create_project_btn.click(core.create_project, inputs=project_name, outputs=[project_dropdown, project_status], queue=False)
    load_project_btn.click(core.load_project, inputs=project_dropdown, outputs=[script, voice_dropdown, model_ui, language_ui, project_name, project_status], queue=False)
    save_project_btn.click(core.save_project, inputs=[project_dropdown, project_name, script, voice_dropdown, model_ui, language_ui, compute_preference], outputs=project_status, queue=False)
    delete_project_btn.click(core.delete_project, inputs=project_dropdown, outputs=[project_dropdown, project_status], queue=False)
    takes_refresh.click(core.project_take_choices, inputs=project_dropdown, outputs=project_takes, queue=False)
    take_load.click(core.load_take, inputs=project_takes, outputs=[output_audio, output_download], queue=False)
    if initial_project:
        demo.load(core.project_take_choices, inputs=project_dropdown, outputs=project_takes, queue=False)

    for component in (script, voice_dropdown, model_ui, language_ui):
        component.change(core.autosave_project, inputs=[project_dropdown, script, voice_dropdown, model_ui, language_ui, compute_preference], outputs=project_status, queue=False)

    history_refresh.click(core.refresh_history, outputs=history, queue=False)
    history_load.click(core.load_history, inputs=history, outputs=[history_audio, history_download, history_metadata, history_note], queue=False)
    history_delete.click(core.delete_history, inputs=history, outputs=[history, history_note], queue=False)

    batch_parse.click(core.preview_batch_file, inputs=batch_file, outputs=[batch_state, batch_table, batch_status], queue=False)
    batch_inputs = [
        batch_state, batch_voice, batch_model, batch_language, speech_speed, compute_preference, offline_mode, no_auto_download,
        exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, top_k,
        raw_mode, smart_chunking, max_chars, chunk_gap, seed,
        normalize_unicode, normalize_punctuation, normalize_numbers, replace_urls, collapse_punctuation, normalize_whitespace,
        quality_check, verify_stt, whisper_model, verification_threshold, auto_retries, best_of_n,
        batch_fit, batch_stretch,
    ]
    batch_run.click(core.run_batch_ui, inputs=batch_inputs, outputs=[batch_manifest, batch_output_dir, batch_status], show_progress="minimal")

    stt_btn.click(core.transcribe_ui, inputs=[stt_audio, stt_quality, stt_language, compute_preference], outputs=[stt_text, stt_segments, stt_status], show_progress="minimal")
    stt_install_btn.click(install_speech_tools_inline, outputs=[stt_status, stt_install_btn], show_progress="minimal")

    model_refresh.click(core.refresh_models, outputs=[models_html, model_action_status], queue=False)
    model_install.click(install_model_explicit, inputs=[model_action, offline_mode], outputs=[models_html, model_action_status], show_progress="minimal")
    model_check.click(core.check_model_update_ui, inputs=[model_action, offline_mode], outputs=[models_html, model_action_status], show_progress="minimal")
    model_update.click(update_model_explicit, inputs=[model_action, offline_mode], outputs=[models_html, model_action_status], show_progress="minimal")
    model_remove.click(core.remove_model_ui, inputs=model_action, outputs=[models_html, model_action_status], queue=False)
    model_load.click(core.load_model_ui, inputs=[model_action, compute_preference, offline_mode, no_auto_download], outputs=[models_html, model_action_status], show_progress="minimal")
    model_unload.click(core.unload_model_ui, outputs=[models_html, model_action_status], queue=False)
    demo.load(core.refresh_models, outputs=[models_html, model_action_status], queue=False)

    save_preferences.click(save_preferences_explicit, inputs=[compute_preference, quality_mode, offline_mode, trim_silence, peak_normalize, fade_ms], outputs=preferences_status, queue=False)
    install_speech_btn.click(core.install_speech_tools, outputs=speech_tools_note, show_progress="minimal")
    diagnostics_refresh.click(core.refresh_hardware, outputs=[hardware_box, diagnostics_box], queue=False)


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
