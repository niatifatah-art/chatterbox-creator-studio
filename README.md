# Chatterbox Creator Studio

> A local, creator-first interface for **Resemble AI's Chatterbox Multilingual V3** — built because I wanted one voice tool I could open, use, and stop fighting with.

Chatterbox Creator Studio turns the excellent open-source Chatterbox V3 model into a small practical studio: save a reference voice once, write a script, insert **exact digital pauses**, tune the delivery, generate, listen, and download the WAV.

It is intentionally focused on **one model** instead of becoming another giant model launcher.

> [!IMPORTANT]
> This is an independent community project. It is **not** an official Resemble AI product and is not endorsed by Resemble AI. The TTS model itself is Chatterbox by Resemble AI — they did the hard part. This project is a creator-oriented local UI and workflow around it.

## Why I built it

I was trying to make voiceovers and kept bouncing between scripts, command lines, different Chatterbox variants, punctuation tricks, and manually adding silence in an editor. I wanted something simpler:

- one reliable Chatterbox model;
- one saved voice instead of finding the reference file every time;
- `Aaaand`, `Buuut`, `Sooo` and normal creator-style writing without a complicated workflow;
- pauses like **0.25 s or 1.375 s that are actually that long**;
- a UI that is pleasant enough to leave open while making a video.

So this is that tool. Small first, useful first.

## What it does

- **Chatterbox Multilingual V3 only** — no model-choice maze.
- **Zero-shot voice cloning** from an uploaded or recorded reference.
- **Voice profiles** saved locally in `data/voices/`.
- **Deterministic pauses** with `[pause=…]` syntax.
- **Creator / Natural / Stable / Expressive** starting presets.
- Full advanced controls for exaggeration, CFG/pacing, temperature, repetition penalty, Min P and Top P.
- Optional **post speech-speed** control that preserves the exact digital pause durations.
- Automatic device selection: CUDA → Apple MPS → CPU.
- Conservative long-text chunking to reduce unstable very-long generations.
- Local output history in `outputs/`.
- No hosted API, account, subscription, or ZeroGPU quota.
- Local Gradio UI with a simple dark creator-focused design.

## Exact pause syntax

Chatterbox's own punctuation preprocessing does not provide deterministic timing. Creator Studio therefore handles pauses **outside the model**.

```text
Aaaand yes, it worked.
[pause=0.35]

Buuut, there is one small problem.
[pause=800ms]

Sooo, let's try again.
```

Supported forms:

```text
[pause=0.25]     # 0.25 seconds
[pause=1.375s]   # 1.375 seconds
[pause=250ms]    # 250 milliseconds
```

At Chatterbox's 24 kHz output rate, a `0.25` second pause is inserted as exactly **6,000 zero-valued samples**. The model never sees the pause token, so it cannot pronounce `[pause]` aloud.

For safety, one pause marker is limited to 30 seconds.

## Where does the model come from?

**The model is not stored in this GitHub repository.**

Creator Studio installs the upstream Chatterbox package from the official `resemble-ai/chatterbox` source and loads V3 explicitly:

```python
ChatterboxMultilingualTTS.from_pretrained(
    device=device,
    t3_model="v3",
)
```

The upstream library then uses Hugging Face Hub to download the required official files from **`ResembleAI/chatterbox`**. Hugging Face stores them in its normal local cache, so after the first download they are reused rather than downloaded for every launch.

The dependency is pinned in `requirements.txt` to a tested upstream source revision so the install does not silently switch to an incompatible Chatterbox implementation.

## Privacy / local-first behavior

The studio server binds to:

```text
127.0.0.1:7860
```

and launches Gradio with `share=False`.

Your saved reference voices and generated WAV files stay in this project directory. The normal first-run network activity is downloading the official model/dependencies. This project does not upload your voice to a server owned by this project.

> Treat voice cloning responsibly: only clone voices you have the right and consent to use.

---

# Install

## Windows 10 / 11

### 1. Install prerequisites

You need:

- **Git**
- **Python 3.11 (64-bit)**

Python 3.11 is the version used for the primary setup because upstream Chatterbox documents development/testing on Python 3.11 and it avoids the dependency surprises that can occur on newer Python versions.

Check:

```powershell
git --version
py -3.11 --version
```

### 2. Clone this repository

```powershell
mkdir C:\AI -ErrorAction SilentlyContinue
cd C:\AI
git clone https://github.com/niatifatah-art/chatterbox-creator-studio.git
cd chatterbox-creator-studio
```

### 3. Run setup

Double-click:

```text
scripts\setup_windows.bat
```

or from PowerShell:

```powershell
.\scripts\setup_windows.bat
```

The script creates a project-local `.venv` and installs everything inside it. It does **not** rely on whatever random `python` happens to be first in your Windows PATH.

### 4. Start the studio

Double-click:

```text
scripts\start_windows.bat
```

The browser should open automatically at:

```text
http://127.0.0.1:7860
```

The **first generation** can take a while because the official model files need to be downloaded. Later launches reuse the Hugging Face cache.

## Linux

Install Git, Python 3.11, and the normal Python venv support for your distro, then:

```bash
git clone https://github.com/niatifatah-art/chatterbox-creator-studio.git
cd chatterbox-creator-studio
bash scripts/setup_linux.sh
bash scripts/start_linux.sh
```

The UI opens locally at `http://127.0.0.1:7860`.

---

# First use

1. Open **Add reference voice**.
2. Upload or record a clean voice sample.
3. Give it a short name such as `My Voice`.
4. Click **Save voice**.
5. Write your script.
6. Add exact timing where useful, e.g. `[pause=0.35]`.
7. Start with the **Creator** preset or **Natural** preset.
8. Click **Generate voice**.
9. Listen in the browser or download the generated WAV.

A clean reference of roughly **10 seconds or more** is a good starting point. Avoid background music, multiple speakers, heavy reverb, or long silence in the reference.

## Presets

Presets are only starting points. You can open **Advanced settings** and change every value.

| Preset | Intent |
|---|---|
| **Natural** | Upstream-like general starting point |
| **Creator** | Slightly slower, more conversational creator delivery |
| **Stable** | More conservative sampling for difficult lines |
| **Expressive** | Higher exaggeration with lower CFG |

The `Creator` preset is a community preset, not an official Resemble AI preset. It intentionally starts with lower CFG/pacing and moderate exaggeration because upstream Chatterbox guidance notes that lower CFG can help fast reference speakers, while expressive speech often benefits from lower CFG plus higher exaggeration.

## Speech speed vs. pause speed

The **Post speech speed** slider time-stretches only generated speech. Explicit pauses are inserted **after** speech post-processing, so:

```text
[pause=0.350]
```

remains 350 ms even if speech is set to `0.94x`.

---

# Files and folders

```text
chatterbox-creator-studio/
├── app.py                    # Gradio UI
├── studio/
│   ├── engine.py             # Chatterbox loading/generation
│   ├── pauses.py             # deterministic pause parser
│   ├── presets.py            # generation presets
│   ├── settings.py           # local settings persistence
│   └── voices.py             # local voice profiles
├── assets/style.css          # UI styling
├── scripts/
│   ├── setup_windows.bat
│   ├── start_windows.bat
│   ├── setup_linux.sh
│   └── start_linux.sh
├── data/
│   ├── voices/               # your private reference voices (gitignored)
│   └── settings.json         # your last settings (gitignored)
├── outputs/                  # generated WAV files (gitignored)
└── tests/                    # model-free core tests
```

Do **not** commit your reference voices unless you deliberately intend to publish them. `.gitignore` excludes them by default.

# CPU and GPU

Creator Studio chooses a device automatically:

1. NVIDIA CUDA if PyTorch can use it;
2. Apple MPS on supported Macs;
3. CPU otherwise.

V3 is a 500M-class model, so CPU generation can be much slower than real time. The UI is still fully usable on CPU; generation simply takes longer.

# Troubleshooting

## `voice.wav.wav`

Windows Explorer can hide known file extensions, which makes it surprisingly easy to rename `voice.wav` into `voice.wav.wav`. Creator Studio avoids requiring a specific filename: save the voice through the UI and it creates the profile path for you.

## The first generation looks frozen

Watch the terminal window. On first use, Hugging Face may be downloading several gigabytes of model files. This is expected. Do not close the process during the download.

## Hugging Face symlink warning on Windows

Hugging Face can warn that Windows symlinks are unavailable unless Developer Mode is enabled. The cache can still work, but may use more disk space. Enabling Windows Developer Mode removes that limitation; it is not required just to generate speech.

## Output is too fast

Try the **Creator** preset first. Then adjust:

- lower **CFG / pacing** toward `0.30`;
- reduce **Post speech speed** (for example `0.94x` or `0.90x`);
- use explicit `[pause=…]` markers rather than hoping punctuation produces a specific duration.

## A long line becomes unstable

Creator Studio conservatively chunks long speech near sentence boundaries before sending it to the model. For best creator pacing, explicit script sections plus `[pause=…]` still give the most predictable result.

## Hallucinations / repetition

Chatterbox Multilingual V3 was released with improved stability and reduced unwanted continuation, repetition, and off-prompt speech compared with earlier multilingual versions, but no generative TTS model can truthfully promise zero errors in every generation.

If one result is bad, regenerate it or split the line into smaller sections. Automatic speech verification/retry is a possible future feature, but is intentionally not required by v0.1 so the studio stays lightweight.

# Development

The core pause parser and chunker can be tested without downloading the TTS model:

```bash
python -m pip install -r requirements-dev.txt
pytest -q
```

GitHub Actions runs these lightweight tests on every push and pull request.

# Credits

The model and core TTS implementation are **Chatterbox by Resemble AI**. This UI would not exist without their open-source work.

- Upstream project: `resemble-ai/chatterbox`
- Model: **Chatterbox Multilingual V3**
- Generated audio includes the upstream **PerTh** neural watermarking behavior.

See [`NOTICE.md`](NOTICE.md) for attribution details.

# License

This Creator Studio code is released under the **MIT License**. Chatterbox and its model assets remain subject to their upstream license and notices.

---

### Current goal

Make voice generation boring in the best possible way: open the studio, pick the voice, type the line, get the WAV, continue making the video.
