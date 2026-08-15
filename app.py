from __future__ import annotations

import os
from pathlib import Path

import gradio as gr

from studio.engine import ChatterboxEngine
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
from studio.presets import PRESETS, preset_values
from studio.settings import SettingsStore
from studio.voices import VoiceLibrary

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
VOICE_DIR = DATA_DIR / "voices"
OUTPUT_DIR = ROOT / "outputs"
CSS_PATH = ROOT / "assets" / "style.css"

VOICE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

voices = VoiceLibrary(VOICE_DIR)
settings_store = SettingsStore(DATA_DIR / "settings.json")
settings = settings_store.load()
engine = ChatterboxEngine(OUTPUT_DIR)

SAMPLE_SCRIPT = """I wanted a cleaner way to make voiceovers, without repeating the same setup every time. [pause=0.45] So I built a small local studio where I can save a voice, control the delivery, add exact pauses, and generate the final WAV without leaving the browser. [pause=0.65] It is still simple, but that is exactly what I wanted."""


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


def add_voice(audio_path: str | None, name: str | None):
    try:
        saved_name, _ = voices.save(audio_path, name)
    except Exception as exc:
        return gr.update(), f"❌ {exc}"
    choices = voices.list()
    return gr.update(choices=choices, value=saved_name), f"✅ Saved voice **{saved_name}** locally."


def refresh_voices():
    choices = voices.list()
    value = choices[0] if choices else None
    return gr.update(choices=choices, value=value)


def apply_preset(name: str):
    return preset_values(name)


def _model_note(model_id: str) -> str:
    spec = MODEL_SPECS[model_id]
    caps = spec.capabilities
    badges = []
    if caps.multilingual:
        badges.append(f"{len(spec.languages)} languages")
    else:
        badges.append("English")
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
            "⚠️ **Raw Chatterbox mode:** the whole script is sent directly to the selected model. "
            "Studio pause parsing and smart chunking are bypassed; tokens such as `[pause=0.5]` may be spoken aloud."
        )
    return (
        "**Studio mode:** exact `[pause=…]` markers are inserted outside the model. "
        "Smart chunking is optional and never changes the saved original script."
    )


def generate_audio(
    script,
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
    progress=gr.Progress(),
):
    voice_path = voices.path_for(voice_name)
    if voice_path is None:
        raise gr.Error("Add and select a voice profile first.")
    if not (script or "").strip():
        raise gr.Error("Write some text first.")

    model_id = model_id_from_name(model_display_name)
    language_id = language_code_from_name(language_name)
    if model_id in {"turbo", "nano"}:
        language_id = "en"

    progress(0.05, desc=f"Preparing {model_name(model_id)}…")
    try:
        result = engine.generate(
            script=script,
            voice_path=voice_path,
            model_id=model_id,
            language_id=language_id,
            exaggeration=float(exaggeration),
            cfg_weight=float(cfg_weight),
            temperature=float(temperature),
            repetition_penalty=float(repetition_penalty),
            min_p=float(min_p),
            top_p=float(top_p),
            top_k=int(top_k),
            speech_speed=float(speech_speed),
            raw_mode=bool(raw_mode),
            smart_chunking=bool(smart_chunking),
            max_chars=int(max_chars),
            chunk_gap_seconds=float(chunk_gap_seconds),
            seed=int(seed),
        )
    except Exception as exc:
        raise gr.Error(str(exc)) from exc

    settings_store.save(
        {
            "voice": voice_name,
            "model_id": model_id,
            "language": language_id,
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
        }
    )

    progress(1.0, desc="Done")
    recent = engine.recent_outputs()
    mode = "Raw" if raw_mode else "Studio"
    return (
        str(result.audio_path),
        str(result.audio_path),
        str(result.metadata_path),
        (
            f"✅ **{result.model_name}** · **{engine.device_label}** · {mode} mode · "
            f"seed `{result.seed}` · {result.chunk_count} generated chunk(s)."
        ),
        gr.update(choices=recent, value=result.audio_path.name),
    )


def refresh_history():
    recent = engine.recent_outputs()
    return gr.update(choices=recent, value=recent[0] if recent else None)


def load_history(filename: str | None):
    path = engine.output_path(filename)
    metadata = engine.metadata_path(filename)
    if path is None:
        return None, None, None
    return str(path), str(path), str(metadata) if metadata else None


def unload_model():
    previous = engine.loaded_model_id
    engine.unload()
    if previous:
        return f"✅ Unloaded **{model_name(previous)}** from memory."
    return "No Chatterbox model is currently loaded."


available_voices = voices.list()
initial_voice = settings.get("voice") if settings.get("voice") in available_voices else (available_voices[0] if available_voices else None)
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
          <p>One local workspace for Chatterbox V3, Turbo and Nano — with saved voices, exact pauses, reproducible takes and controls that adapt to the model you actually selected.</p>
          <div class="model-strip">
            <span>V3 <small>multilingual</small></span>
            <span>Turbo <small>fast + expressive</small></span>
            <span>Nano <small>small + CPU-friendly</small></span>
          </div>
        </div>
        """
    )

    with gr.Row(equal_height=False):
        with gr.Column(scale=7, elem_classes="main-panel"):
            with gr.Row():
                raw_mode = gr.Checkbox(
                    label="Raw Chatterbox",
                    value=bool(settings["raw_mode"]),
                    info="Bypass Studio pause parsing and chunking.",
                )
                smart_chunking = gr.Checkbox(
                    label="Smart chunking",
                    value=bool(settings["smart_chunking"]),
                    info="Split long text near multilingual sentence boundaries.",
                )

            mode_status = gr.Markdown(mode_note(bool(settings["raw_mode"])), elem_classes="mode-note")

            script = gr.Textbox(
                label="Script",
                value=SAMPLE_SCRIPT,
                lines=16,
                max_lines=28,
                placeholder="Write your script here. Use [pause=0.35] in Studio mode for exact silence.",
                elem_id="script-box",
            )

            gr.Markdown("**Quick inserts** · deterministic silence in Studio mode.")
            with gr.Row():
                for label, token in [
                    ("+0.25s", "[pause=0.25]"),
                    ("+0.5s", "[pause=0.5]"),
                    ("+1s", "[pause=1]"),
                    ("+2s", "[pause=2]"),
                ]:
                    button = gr.Button(label, size="sm", variant="secondary")
                    button.click(make_insert_handler(token), inputs=script, outputs=script, queue=False)

            with gr.Column(visible=initial_caps.supports_paralinguistic_tags) as expression_tools:
                gr.Markdown("**Turbo / Nano expression tags** · native model tokens.")
                with gr.Row():
                    for token in PARALINGUISTIC_TAGS:
                        button = gr.Button(token, size="sm", variant="secondary")
                        button.click(make_insert_handler(token), inputs=script, outputs=script, queue=False)

            generate_btn = gr.Button("Generate voice", variant="primary", size="lg", elem_id="generate-btn")
            output_audio = gr.Audio(label="Result", interactive=False, format="wav")
            with gr.Row():
                output_file = gr.File(label="Download WAV", interactive=False)
                metadata_file = gr.File(label="Generation metadata", interactive=False)
            status = gr.Markdown(
                f"**Device:** {engine.device_label} · Models download from the official Resemble AI repositories on first use and are then reused from the local Hugging Face cache."
            )

            with gr.Accordion("Recent generations", open=False):
                history = gr.Dropdown(label="Saved locally", choices=engine.recent_outputs())
                with gr.Row():
                    history_refresh = gr.Button("Refresh", size="sm")
                    history_load = gr.Button("Load", size="sm")

        with gr.Column(scale=4, elem_classes="side-panel"):
            gr.Markdown("### Model")
            model = gr.Dropdown(
                label="Chatterbox model",
                choices=model_choices(),
                value=initial_model_name,
                allow_custom_value=False,
            )
            model_info = gr.Markdown(_model_note(initial_model_id), elem_classes="model-note")
            language = gr.Dropdown(
                label="Language",
                choices=initial_spec.language_choices(),
                value=initial_language_name,
                allow_custom_value=False,
            )

            gr.Markdown("### Voice profile")
            voice_dropdown = gr.Dropdown(
                label="Saved voice",
                choices=available_voices,
                value=initial_voice,
                allow_custom_value=False,
            )
            voice_audio = gr.Audio(
                label="Add reference voice",
                sources=["upload", "microphone"],
                type="filepath",
                format="wav",
            )
            voice_name = gr.Textbox(label="Voice name", placeholder="My Voice")
            with gr.Row():
                save_voice_btn = gr.Button("Save voice", variant="secondary")
                refresh_voice_btn = gr.Button("Refresh", size="sm")
            voice_status = gr.Markdown("Reference voices stay in `data/voices/` on this computer.")

            gr.Markdown("### Generation")
            preset = gr.Dropdown(label="Preset", choices=list(PRESETS), value=initial_preset)

            with gr.Accordion("Advanced generation", open=False):
                exaggeration = gr.Slider(
                    0.0,
                    1.5,
                    value=float(settings["exaggeration"]),
                    step=0.05,
                    label="Exaggeration",
                    visible=initial_caps.supports_exaggeration,
                )
                cfg_weight = gr.Slider(
                    0.0,
                    1.0,
                    value=float(settings["cfg_weight"]),
                    step=0.05,
                    label="CFG / pacing",
                    visible=initial_caps.supports_cfg,
                )
                temperature = gr.Slider(0.05, 1.5, value=float(settings["temperature"]), step=0.05, label="Temperature")
                repetition_penalty = gr.Slider(1.0, 2.0, value=float(settings["repetition_penalty"]), step=0.05, label="Repetition penalty")
                min_p = gr.Slider(
                    0.0,
                    0.5,
                    value=float(settings["min_p"]),
                    step=0.01,
                    label="Min P",
                    visible=initial_caps.supports_min_p,
                )
                top_p = gr.Slider(0.1, 1.0, value=float(settings["top_p"]), step=0.01, label="Top P")
                top_k = gr.Slider(
                    50,
                    2000,
                    value=int(settings["top_k"]),
                    step=50,
                    label="Top K",
                    visible=initial_caps.supports_top_k,
                )
                seed = gr.Number(
                    value=int(settings["seed"]),
                    precision=0,
                    label="Seed",
                    info="Use -1 for a fresh random seed. The actual seed is saved with every WAV.",
                )
                speech_speed = gr.Slider(0.75, 1.25, value=float(settings["speech_speed"]), step=0.01, label="Post speech speed")
                gr.Markdown("Keep **1.00x** for the cleanest output. This is post-processing, not a native Chatterbox speed control.")

            with gr.Accordion("Long-form controls", open=False):
                max_chars = gr.Slider(
                    80,
                    500,
                    value=int(settings["max_chars"]),
                    step=10,
                    label="Target maximum characters per chunk",
                )
                chunk_gap = gr.Slider(
                    0.0,
                    1.0,
                    value=float(settings["chunk_gap_seconds"]),
                    step=0.01,
                    label="Digital gap between automatic chunks (seconds)",
                )
                gr.Markdown("These controls are ignored in **Raw Chatterbox** mode.")

            with gr.Accordion("Memory", open=False):
                unload_btn = gr.Button("Unload current model", size="sm")
                unload_status = gr.Markdown("No model is loaded until the first generation.")

            gr.Markdown(
                """
                <div class="tip-card">
                <b>Exact pauses</b><br>
                <code>[pause=0.35]</code> · <code>[pause=250ms]</code> · <code>[pause=2s]</code><br><br>
                Studio mode inserts real zero-valued samples after generation. Raw mode intentionally bypasses this behavior.
                </div>
                """
            )

    gr.Markdown(
        """
        <div class="footer-note">
        Powered by <b>Chatterbox</b> by Resemble AI. Independent community project; not an official Resemble AI product.
        </div>
        """
    )

    raw_mode.change(mode_note, inputs=raw_mode, outputs=mode_status, queue=False)
    save_voice_btn.click(add_voice, inputs=[voice_audio, voice_name], outputs=[voice_dropdown, voice_status])
    refresh_voice_btn.click(refresh_voices, outputs=voice_dropdown, queue=False)
    preset.change(
        apply_preset,
        inputs=preset,
        outputs=[exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, speech_speed],
        queue=False,
    )
    model.change(
        on_model_change,
        inputs=[model, language],
        outputs=[language, model_info, exaggeration, cfg_weight, min_p, top_k, expression_tools],
        queue=False,
    )
    unload_btn.click(unload_model, outputs=unload_status, queue=False)
    generate_btn.click(
        generate_audio,
        inputs=[
            script,
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
        ],
        outputs=[output_audio, output_file, metadata_file, status, history],
    )
    history_refresh.click(refresh_history, outputs=history, queue=False)
    history_load.click(load_history, inputs=history, outputs=[output_audio, output_file, metadata_file], queue=False)


def main() -> None:
    css = CSS_PATH.read_text(encoding="utf-8") if CSS_PATH.exists() else ""
    demo.queue(default_concurrency_limit=1).launch(
        server_name=os.getenv("CHATTERBOX_HOST", "127.0.0.1"),
        server_port=int(os.getenv("CHATTERBOX_PORT", "7860")),
        inbrowser=True,
        share=False,
        show_error=True,
        theme=gr.themes.Soft(),
        css=css,
    )


if __name__ == "__main__":
    main()
