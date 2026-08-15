# Chatterbox Creator Studio

> A local, creator-first workspace for **Resemble AI Chatterbox** — currently Multilingual V3, Turbo and Nano.

Creator Studio takes the open-source Chatterbox models and gives them one practical local workflow: save a reference voice, write a script, choose the model that fits the job, generate, listen, download the WAV, and keep enough metadata to reproduce the take later.

> [!IMPORTANT]
> This is an independent community project. It is **not** an official Resemble AI product and is not endorsed by Resemble AI. The Chatterbox models and core TTS implementation are Resemble AI's work; Creator Studio is a local UI/workflow and reliability layer around them.

## v0.2 foundation

The v0.2 work changes the project from a V3-specific Gradio wrapper into a model-aware local studio.

### Models

| Model | Best for | Language | Studio behavior |
|---|---|---|---|
| **Chatterbox Multilingual V3** | general creator voiceovers, cross-language cloning | 23 languages | CFG, exaggeration, Min P and multilingual controls |
| **Chatterbox Turbo** | fast expressive English | English | native expression tags and Top K |
| **Chatterbox Nano** | smaller / CPU-friendly local generation | English | Turbo-style tags and Top K |

The model is selected through a registry, so future Chatterbox variants can be added without turning the UI into model-specific spaghetti.

### Raw Chatterbox mode

Creator Studio now has an explicit **Raw Chatterbox** option.

Raw mode intentionally bypasses Studio exact-pause parsing and automatic chunking and sends the whole script directly to the selected model. It exists so creator conveniences never become mandatory behavior.

Studio mode remains the recommended workflow for normal creator use.

### Exact pauses

In Studio mode:

```text
This is the first line. [pause=0.35] This starts exactly 350 ms later.
```

Supported forms:

```text
[pause=0.25]
[pause=1.375s]
[pause=250ms]
```

The pause is inserted as real zero-valued audio samples **outside** Chatterbox, so the model never needs to understand a `[pause]` token.

### Multilingual smart chunking

Long-form text can be split near sentence boundaries before synthesis. The v0.2 chunker recognizes normal Latin punctuation plus Arabic/CJK boundaries such as:

```text
؟ 。 ！ ？
```

It also tries to avoid tiny final fragments, which are undesirable for generative TTS. Smart chunking is optional and is ignored in Raw mode.

### Reproducible takes

Every WAV gets a JSON sidecar with the generation details:

```text
outputs/
├── multilingual-v3_2026-08-15_16-20-00-123.wav
└── multilingual-v3_2026-08-15_16-20-00-123.json
```

The metadata records the model, voice file, language, actual seed, parameters, original script and chunks sent to the model. Use seed `-1` for a fresh random seed; the chosen seed is still saved after generation.

### Capability-aware controls

The UI adapts to the model:

- V3 exposes multilingual language selection, exaggeration, CFG and Min P.
- Turbo/Nano lock generation to English, hide unsupported V3 controls and expose Top K.
- Turbo/Nano show native expression-tag shortcuts such as `[laugh]`, `[chuckle]` and `[cough]`.

## Local-first behavior

The studio binds to:

```text
127.0.0.1:7860
```

and launches Gradio with `share=False`.

Saved reference voices, settings, generated WAVs and metadata remain in the project directory. Normal first-run network activity is downloading the official Chatterbox model files/dependencies; Hugging Face caches them for later reuse.

> Only clone voices you have the right and consent to use.

---

# Install

## Windows 10 / 11

You need:

- **Git**
- **Python 3.11 (64-bit)**

Check:

```powershell
git --version
py -3.11 --version
```

Clone the repository:

```powershell
mkdir C:\AI -ErrorAction SilentlyContinue
cd C:\AI
git clone https://github.com/niatifatah-art/chatterbox-creator-studio.git
cd chatterbox-creator-studio
```

Run setup:

```powershell
.\scripts\setup_windows.bat
```

Then start:

```powershell
.\scripts\start_windows.bat
```

The browser should open at `http://127.0.0.1:7860`.

## Linux

```bash
git clone https://github.com/niatifatah-art/chatterbox-creator-studio.git
cd chatterbox-creator-studio
bash scripts/setup_linux.sh
bash scripts/start_linux.sh
```

The UI opens locally at `http://127.0.0.1:7860`.

---

# First use

1. Add or record a clean reference voice.
2. Save the voice profile.
3. Choose **V3**, **Turbo**, or **Nano**.
4. Choose the language when using V3.
5. Write the script.
6. Leave **Raw Chatterbox** off for normal Studio behavior.
7. Add `[pause=...]` markers where exact timing matters.
8. Generate and listen.
9. Download the WAV; keep the metadata JSON if you want exact settings later.

A clean reference of roughly 10 seconds or more is a good starting point. Avoid background music, multiple speakers, heavy reverb, clipping, or long silence in the reference.

## Presets

Presets are starting points, not hidden magic. Advanced controls remain available where the selected model supports them.

| Preset | Intent |
|---|---|
| **Natural** | upstream-like general starting point |
| **Creator** | conversational V3 starting point with lower CFG/pacing |
| **Stable** | more conservative sampling for difficult lines |
| **Expressive** | higher V3 exaggeration with lower CFG |

All presets keep **Post speech speed at `1.00x` by default**. Non-1.00 values use waveform time stretching and can introduce metallic or phasey artifacts.

## Long-form controls

Studio mode exposes:

- target maximum characters per automatic chunk;
- digital gap between automatic chunks;
- a Smart chunking on/off switch.

Raw mode bypasses all three.

## Memory

Only one Chatterbox model is kept active by Creator Studio at a time. Switching models unloads the previous adapter, and the UI also provides an **Unload current model** button for users who want to free RAM/VRAM manually.

---

# Project structure

```text
chatterbox-creator-studio/
├── app.py                    # Gradio UI / orchestration only
├── studio/
│   ├── engine.py             # model-independent generation pipeline
│   ├── models.py             # model registry + V3/Turbo/Nano adapters
│   ├── text.py               # multilingual smart chunking
│   ├── pauses.py             # deterministic pause parser
│   ├── presets.py            # creator presets
│   ├── settings.py           # local settings persistence
│   └── voices.py             # local voice profiles
├── assets/style.css
├── docs/ARCHITECTURE.md
├── scripts/
├── data/voices/              # private reference voices (gitignored)
├── outputs/                  # generated WAV + JSON metadata (gitignored)
└── tests/                    # model-free core tests
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the design rules behind the v0.2 refactor.

# CPU and GPU

Device selection is automatic:

1. NVIDIA CUDA when available;
2. Apple MPS on supported Macs;
3. CPU otherwise.

Nano is the model intended for the smallest resource budget. V3 is much heavier, and CPU generation can be substantially slower than real time.

# Troubleshooting

## First generation looks frozen

Watch the terminal. The selected official model may still be downloading from Hugging Face. Later launches reuse the local cache.

## Output is too fast

For V3, start with the **Creator** preset and use exact pauses where timing matters. Keep Post speech speed at `1.00x` unless you intentionally want waveform time stretching.

## Output sounds metallic or phasey

Set **Post speech speed** to exactly `1.00x` and regenerate before changing the model controls. Time stretching can introduce artifacts that are not present in the underlying Chatterbox output.

## A short phrase hallucinates

Generative TTS can be unstable on tiny fragments. In Studio mode, leave Smart chunking enabled for long scripts so short neighboring sentences can be combined where possible. Raw mode is available when you deliberately want upstream behavior without Studio assistance.

## I wrote `[pause=0.5]` and the model spoke it

Check **Raw Chatterbox**. Exact Studio pause parsing is intentionally disabled in Raw mode.

# Development

Core tests do not download a TTS model:

```bash
python -m pip install -r requirements-dev.txt
pytest -q
```

GitHub Actions runs the lightweight tests on pushes and pull requests.

# Roadmap

After the v0.2 foundation is stable, the planned local-only layers are:

- Projects and Takes;
- richer Voice Library and reference diagnostics;
- Batch / SRT / VTT workflows;
- optional bad-generation detection and retry;
- optional local STT verification;
- optional text normalization and audio post-processing;
- model manager, diagnostics and packaging polish.

These features will be built on the core rather than silently forced into generation.

# Credits

The models and core TTS implementation are **Chatterbox by Resemble AI**. This UI would not exist without their open-source work.

The upstream package is pinned to a tested source revision so V3/Turbo/Nano support does not silently change underneath Creator Studio.

Generated audio retains the upstream Chatterbox watermarking behavior.

See [`NOTICE.md`](NOTICE.md) for attribution details.

# License

Creator Studio code is released under the **MIT License**. Chatterbox model/package assets remain subject to their upstream licenses and notices.
