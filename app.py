from __future__ import annotations

import os
from pathlib import Path

import gradio as gr

from studio.engine import ChatterboxEngine
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

LANGUAGES = {
    "English": "en",
    "Arabic": "ar",
    "Danish": "da",
    "German": "de",
    "Greek": "el",
    "Spanish": "es",
    "Finnish": "fi",
    "French": "fr",
    "Hebrew": "he",
    "Hindi": "hi",
    "Italian": "it",
    "Japanese": "ja",
    "Korean": "ko",
    "Malay": "ms",
    "Dutch": "nl",
    "Norwegian": "no",
    "Polish": "pl",
    "Portuguese": "pt",
    "Russian": "ru",
    "Swedish": "sv",
    "Swahili": "sw",
    "Turkish": "tr",
    "Chinese": "zh",
}
LANGUAGE_NAMES_BY_CODE = {code: name for name, code in LANGUAGES.items()}

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


def generate_audio(
    script,
    voice_name,
    language_name,
    preset_name,
    exaggeration,
    cfg_weight,
    temperature,
    repetition_penalty,
    min_p,
    top_p,
    speech_speed,
    progress=gr.Progress(),
):
    voice_path = voices.path_for(voice_name)
    if voice_path is None:
        raise gr.Error("Add and select a voice profile first.")
    if not (script or "").strip():
        raise gr.Error("Write some text first.")

    language_id = LANGUAGES.get(language_name, "en")
    progress(0.05, desc="Loading Chatterbox Multilingual V3…")
    try:
        output = engine.generate(
            script=script,
            voice_path=voice_path,
            language_id=language_id,
            exaggeration=float(exaggeration),
            cfg_weight=float(cfg_weight),
            temperature=float(temperature),
            repetition_penalty=float(repetition_penalty),
            min_p=float(min_p),
            top_p=float(top_p),
            speech_speed=float(speech_speed),
        )
    except Exception as exc:
        raise gr.Error(str(exc)) from exc

    settings_store.save(
        {
            "voice": voice_name,
            "language": language_id,
            "preset": preset_name,
            "exaggeration": float(exaggeration),
            "cfg_weight": float(cfg_weight),
            "temperature": float(temperature),
            "repetition_penalty": float(repetition_penalty),
            "min_p": float(min_p),
            "top_p": float(top_p),
            "speech_speed": float(speech_speed),
        }
    )
    progress(1.0, desc="Done")
    recent = engine.recent_outputs()
    return (
        str(output),
        str(output),
        f"✅ Generated with **Chatterbox Multilingual V3** on **{engine.device_label}**. Saved to `{output.name}`.",
        gr.update(choices=recent, value=output.name),
    )


def refresh_history():
    recent = engine.recent_outputs()
    return gr.update(choices=recent, value=recent[0] if recent else None)


def load_history(filename: str | None):
    path = engine.output_path(filename)
    if path is None:
        return None, None
    return str(path), str(path)


available_voices = voices.list()
initial_voice = settings.get("voice") if settings.get("voice") in available_voices else (available_voices[0] if available_voices else None)
initial_language = LANGUAGE_NAMES_BY_CODE.get(settings.get("language", "en"), "English")
initial_preset = settings.get("preset", "Creator") if settings.get("preset") in PRESETS else "Creator"

with gr.Blocks(title="Chatterbox Creator Studio", analytics_enabled=False) as demo:
    gr.HTML(
        """
        <div class="hero">
          <div class="eyebrow">LOCAL • CREATOR-FIRST • OPEN SOURCE</div>
          <h1>Chatterbox Creator Studio</h1>
          <p>A clean local studio for Resemble AI's Chatterbox Multilingual V3 — voice cloning, exact digital pauses, creator presets, and zero cloud quota.</p>
        </div>
        """
    )

    with gr.Row(equal_height=False):
        with gr.Column(scale=7, elem_classes="main-panel"):
            script = gr.Textbox(
                label="Script",
                value=SAMPLE_SCRIPT,
                lines=15,
                max_lines=24,
                placeholder="Write your script here. Use [pause=0.35] for exact silence.",
                elem_id="script-box",
            )
            gr.Markdown("**Quick inserts** · exact digital silence, not punctuation tricks.")
            with gr.Row():
                for label, token in [
                    ("+0.25s", "[pause=0.25]"),
                    ("+0.5s", "[pause=0.5]"),
                    ("+1s", "[pause=1]"),
                    ("+2s", "[pause=2]"),
                ]:
                    button = gr.Button(label, size="sm", variant="secondary")
                    button.click(make_insert_handler(token), inputs=script, outputs=script, queue=False)

            generate_btn = gr.Button("Generate voice", variant="primary", size="lg", elem_id="generate-btn")
            output_audio = gr.Audio(label="Result", interactive=False, format="wav")
            output_file = gr.File(label="Download WAV", interactive=False)
            status = gr.Markdown(
                f"**Model:** Chatterbox Multilingual V3 · **Device:** {engine.device_label} · First generation downloads the official model if it is not cached."
            )

            with gr.Accordion("Recent generations", open=False):
                history = gr.Dropdown(label="Saved locally", choices=engine.recent_outputs())
                with gr.Row():
                    history_refresh = gr.Button("Refresh", size="sm")
                    history_load = gr.Button("Load", size="sm")

        with gr.Column(scale=4, elem_classes="side-panel"):
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
            voice_status = gr.Markdown("Reference voices are saved only in `data/voices/` on this computer.")

            gr.Markdown("### Generation")
            language = gr.Dropdown(label="Language", choices=list(LANGUAGES), value=initial_language)
            preset = gr.Dropdown(label="Preset", choices=list(PRESETS), value=initial_preset)

            with gr.Accordion("Advanced settings", open=False):
                exaggeration = gr.Slider(0.0, 1.5, value=float(settings["exaggeration"]), step=0.05, label="Exaggeration")
                cfg_weight = gr.Slider(0.0, 1.0, value=float(settings["cfg_weight"]), step=0.05, label="CFG / pacing")
                temperature = gr.Slider(0.05, 1.5, value=float(settings["temperature"]), step=0.05, label="Temperature")
                repetition_penalty = gr.Slider(1.0, 2.0, value=float(settings["repetition_penalty"]), step=0.05, label="Repetition penalty")
                min_p = gr.Slider(0.0, 0.5, value=float(settings["min_p"]), step=0.01, label="Min P")
                top_p = gr.Slider(0.1, 1.0, value=float(settings["top_p"]), step=0.01, label="Top P")
                speech_speed = gr.Slider(0.75, 1.25, value=float(settings["speech_speed"]), step=0.01, label="Post speech speed")
                gr.Markdown("Keep **1.00x** for the cleanest output. Non-1.00 values use post-processing and may introduce metallic or phasey artifacts. Exact `[pause=…]` durations are preserved either way.")

            gr.Markdown(
                """
                <div class="tip-card">
                <b>Pause syntax</b><br>
                <code>[pause=0.35]</code> · <code>[pause=250ms]</code> · <code>[pause=2s]</code><br><br>
                The pause is inserted as actual zero-valued audio samples after generation.
                </div>
                """
            )

    gr.Markdown(
        """
        <div class="footer-note">
        Powered by <b>Chatterbox</b> by Resemble AI. This is an independent community UI, not an official Resemble AI product.
        </div>
        """
    )

    save_voice_btn.click(add_voice, inputs=[voice_audio, voice_name], outputs=[voice_dropdown, voice_status])
    refresh_voice_btn.click(refresh_voices, outputs=voice_dropdown, queue=False)
    preset.change(
        apply_preset,
        inputs=preset,
        outputs=[exaggeration, cfg_weight, temperature, repetition_penalty, min_p, top_p, speech_speed],
        queue=False,
    )
    generate_btn.click(
        generate_audio,
        inputs=[
            script,
            voice_dropdown,
            language,
            preset,
            exaggeration,
            cfg_weight,
            temperature,
            repetition_penalty,
            min_p,
            top_p,
            speech_speed,
        ],
        outputs=[output_audio, output_file, status, history],
    )
    history_refresh.click(refresh_history, outputs=history, queue=False)
    history_load.click(load_history, inputs=history, outputs=[output_audio, output_file], queue=False)


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
