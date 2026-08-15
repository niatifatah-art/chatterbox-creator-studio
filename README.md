# Chatterbox Creator Studio

> A local, creator-first production workspace for **Resemble AI Chatterbox** — Multilingual V3, Turbo and Nano.

Chatterbox Creator Studio turns the open-source Chatterbox family into a practical local voiceover workflow: keep projects and takes, save and inspect reference voices, write or batch scripts, add exact pauses, generate reproducible candidates, optionally verify them, and export the result without requiring an account, hosted API or telemetry.

> [!IMPORTANT]
> This is an independent community project. It is **not** an official Resemble AI product and is not endorsed by Resemble AI. Chatterbox and the model implementation are Resemble AI's work; Creator Studio is an independent local UI, workflow and reliability layer around them.

## What is included

### Studio

- **Chatterbox Multilingual V3**, **Turbo** and **Nano** behind one model registry.
- Capability-aware controls: unsupported controls disappear instead of being silently ignored.
- Explicit **Raw Chatterbox** mode for users who want upstream behavior without Studio pause parsing, text preprocessing or smart chunking.
- Exact digital pauses such as `[pause=0.35]`, `[pause=250ms]` and `[pause=1.2s]`.
- Multilingual long-text chunking with Latin, Arabic and CJK sentence boundaries.
- Reproducible seeds plus a JSON sidecar for every generated WAV.
- Turbo/Nano expression-tag shortcuts such as `[laugh]`, `[chuckle]`, `[sigh]` and `[cough]`.
- Model unload control so users can explicitly release RAM/VRAM.

### Projects and Takes

Projects live in `data/projects/` and keep the creator workflow local. A project stores its script, selected voice/model/language and a settings snapshot. Studio generations can be copied into the project as independent takes, so a new attempt does not destroy the previous one.

```text
data/projects/my-video/
├── project.json
└── takes/
    └── full/
        ├── 20260815-194000-001.wav
        └── 20260815-194000-001.json
```

### Voice Library

Saved voice references can be:

- recorded or uploaded;
- previewed;
- renamed;
- duplicated;
- deleted;
- inspected for duration, sample rate, silence, clipping and level warnings.

Reference analysis is advisory. It never blocks generation.

> Only clone voices you have the right and consent to use.

### Batch and subtitles

The Batch tab accepts:

- TXT / Markdown;
- CSV;
- JSON;
- SRT;
- VTT.

SRT/VTT timing is parsed into target durations. Optional duration fitting uses conservative time stretching; if the required stretch is outside the configured safe range, Creator Studio keeps the valid raw take and records a timing warning instead of throwing it away.

### Optional text helpers

Text transformations are **off unless you choose them**. Available helpers include:

- Unicode normalization;
- punctuation normalization;
- optional numbers-to-words via `num2words`;
- URL replacement;
- repeated-punctuation cleanup;
- whitespace normalization.

The Studio includes a **processed-text preview** so you can see what will be sent to the generation pipeline before you generate.

Raw Chatterbox mode disables Studio text preprocessing automatically.

### Optional reliability layer

The normal generation path does not require an STT model. When enabled, Creator Studio can add:

- inexpensive local output checks for suspicious silence, clipping, tail silence and broken/very short audio;
- automatic retry;
- Best-of-N candidate generation and selection;
- optional Faster-Whisper transcription verification against the source text.

Best-of-N always has an actual ranking signal: if you request multiple candidates without enabling STT or the visible quality-check toggle, Creator Studio uses the lightweight non-transforming audio quality score for ranking and records that fact in metadata.

Faster-Whisper is loaded only when requested and its verifier model is cached in-process after the first load.

### Optional audio finishing

Creator Studio can optionally:

- trim leading/trailing silence;
- peak-normalize;
- add a short fade in/out;
- fit subtitle takes to a target duration within a safe stretch range.

These operations are off by default.

### History, diagnostics and model cache

The UI includes:

- recent WAV/metadata history with preview and deletion;
- Python / PyTorch / CUDA / MPS / GPU / VRAM / disk / FFmpeg / Git diagnostics;
- Hugging Face Chatterbox cache inspection;
- safe deletion of detected Chatterbox cache entries;
- explicit model unload.

Deleting a model cache entry never deletes voices or projects; the model simply downloads again when needed.

---

# Models

| Model | Best for | Language | Notable Studio behavior |
|---|---|---|---|
| **Chatterbox Multilingual V3** | general creator voiceovers, multilingual cloning | 23 languages | language selector, CFG, exaggeration, Min P |
| **Chatterbox Turbo** | fast expressive English | English | native expression tags, Top K |
| **Chatterbox Nano** | smaller / CPU-friendly generation | English | Turbo-style tags, Top K, smallest footprint |

Only one Chatterbox adapter is kept active by Creator Studio at a time.

# Raw mode vs Studio mode

## Studio mode

Studio mode can parse exact pauses, smart-chunk long text and use whichever optional helpers you explicitly enable.

```text
The first sentence ends here. [pause=0.35] This starts exactly 350 ms later.
```

The pause is inserted as zero-valued audio samples **outside** Chatterbox, so the model never needs to understand a `[pause]` token.

## Raw Chatterbox

Raw mode:

- sends the whole script directly to the selected Chatterbox model;
- does not parse Studio pause markers;
- does not smart-chunk;
- disables Studio text preprocessing;
- still lets you separately opt into non-text-changing output checks if you want them.

It exists so creator conveniences never become mandatory behavior.

---

# Install

## Windows 10 / 11

Requirements:

- Git
- Python 3.11 64-bit

```powershell
git --version
py -3.11 --version
mkdir C:\AI -ErrorAction SilentlyContinue
cd C:\AI
git clone https://github.com/niatifatah-art/chatterbox-creator-studio.git
cd chatterbox-creator-studio
.\scripts\setup_windows.bat
.\scripts\start_windows.bat
```

## Linux

```bash
git clone https://github.com/niatifatah-art/chatterbox-creator-studio.git
cd chatterbox-creator-studio
bash scripts/setup_linux.sh
bash scripts/start_linux.sh
```

The default local address is:

```text
http://127.0.0.1:7860
```

The normal application binds locally and does not create a public Gradio share link unless you explicitly change the environment setting.

## Optional helpers

The main install intentionally does **not** pull in Faster-Whisper or number-normalization extras.

After activating the Studio virtual environment, install them only if you want those features:

```bash
python -m pip install -r requirements-optional.txt
```

This enables:

- Faster-Whisper local verification;
- `num2words` number normalization where the selected language is supported.

---

# First use

1. Open **Voices**, upload or record a clean reference, and save it.
2. Optionally create a **Project**.
3. Go to **Studio**, choose V3, Turbo or Nano and select the saved voice.
4. Write the script and add `[pause=...]` markers if exact timing matters.
5. Leave the helper options off for a simple generation, or explicitly enable the ones you want.
6. Generate, listen, compare takes, and keep the WAV + metadata you prefer.

A clean single-speaker reference around 8–15 seconds is a useful starting point. Avoid music, heavy reverb, clipping and large silent sections when possible.

# CLI

The same local core can be used without Gradio:

```bash
python -m studio models
python -m studio voices
python -m studio diagnostics
```

Generate one take:

```bash
python -m studio generate --voice "My-Voice" --text "Hello from Creator Studio." --model nano
```

Raw mode:

```bash
python -m studio generate --voice "My-Voice" --text "Raw model input." --model nano --raw
```

Batch example:

```bash
python -m studio batch subtitles.srt --voice "My-Voice" --model multilingual-v3 --language en --fit-timing
```

# Output and reproducibility

Normal generations are written under `outputs/` as a WAV plus same-name JSON metadata:

```text
outputs/
├── multilingual-v3_2026-08-15_19-40-00-123.wav
└── multilingual-v3_2026-08-15_19-40-00-123.json
```

Metadata includes the model, voice, language, actual seed, generation parameters, Raw/Studio mode, original script and generated chunks. Reliability runs additionally record candidate scoring, quality results and STT verification results when enabled.

Use seed `-1` for a fresh random seed. Creator Studio still records the actual seed chosen for that take.

# Device selection

Creator Studio selects the first available backend in this order:

1. NVIDIA CUDA;
2. Apple MPS;
3. CPU.

Nano is the smallest model option. V3 is substantially heavier, and CPU generation can be much slower than real time.

# Project structure

```text
chatterbox-creator-studio/
├── app.py                       # Gradio product UI and orchestration
├── studio/
│   ├── engine.py                # model-independent generation pipeline
│   ├── models.py                # model registry + V3/Turbo/Nano adapters
│   ├── text.py                  # multilingual smart chunking
│   ├── pauses.py                # deterministic pause parser
│   ├── preprocess.py            # optional visible text transformations
│   ├── quality.py               # audio QC + optional STT verification
│   ├── reliability.py           # retry / Best-of-N orchestration
│   ├── audio.py                 # optional local audio finishing
│   ├── projects.py              # projects and takes
│   ├── voices.py                # voice profiles + reference analysis
│   ├── batch.py                 # TXT/CSV/JSON/SRT/VTT parsing
│   ├── batch_runner.py          # batch generation and timing fit
│   ├── diagnostics.py           # local environment report
│   ├── model_cache.py           # safe Chatterbox cache inspection
│   ├── settings.py              # local settings persistence
│   └── cli.py                   # command-line interface
├── assets/style.css
├── docs/ARCHITECTURE.md
├── scripts/
├── data/voices/                 # private references, gitignored
├── data/projects/               # private local projects, gitignored
├── outputs/                     # generated audio/metadata, gitignored
└── tests/
```

# Troubleshooting

### First generation looks frozen

Watch the terminal. Chatterbox may be downloading model files from Hugging Face. Later runs reuse the cache.

### `[pause=0.5]` is spoken aloud

Check whether **Raw Chatterbox** is enabled. Exact Studio pause parsing is intentionally bypassed in Raw mode.

### Output sounds metallic after changing speed

Set Post speech speed to `1.00x` and regenerate. Time stretching is post-processing and can introduce artifacts.

### Faster-Whisper verification says it is unavailable

Install the optional extras:

```bash
python -m pip install -r requirements-optional.txt
```

### Subtitle fitting was skipped

The generated take required more time stretching than your configured safe limit. Creator Studio deliberately keeps the raw valid WAV instead of heavily distorting it.

# Development and validation

Lightweight tests do not download TTS weights:

```bash
python -m pip install -r requirements-dev.txt
pytest -q
```

The repository also contains real-model smoke tests for V3, Turbo and Nano. Those checks exercise actual CPU model loading, voice conditioning, generation, WAV/JSON writing, deterministic Studio pauses, model unloading and the Raw mode path. The UI smoke test starts Gradio and probes the local HTTP server.

Passing Linux CPU CI does **not** by itself prove every CUDA, Windows or Apple MPS driver combination; those remain hardware-specific integration surfaces.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the internal design rules.

# Privacy and safety

Creator Studio is local-first. Saved voice references, projects and generated audio stay in the project directories unless you move or share them yourself. There is no application account or analytics requirement.

Only use voice cloning with appropriate rights and consent. Creator Studio does not remove upstream Chatterbox watermarking behavior.

# Credits

The models and core TTS implementation are **Chatterbox by Resemble AI**. Creator Studio would not exist without that open-source work.

The upstream Chatterbox package is pinned to a tested source revision so model behavior does not silently change underneath the Studio.

See [`NOTICE.md`](NOTICE.md) for attribution details.

# License

Creator Studio code is released under the **MIT License**. Chatterbox package/model assets remain subject to their upstream licenses and notices.
