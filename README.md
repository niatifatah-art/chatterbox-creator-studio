# Creator Studio

A calm, local workspace for creating and managing AI voiceovers.

The app is designed around a simple rule: **the common path should stay simple, while technical control remains available when you want it.** Choose a voice, write a script, and generate. The app can choose a suitable model and compute backend automatically, while Models and Expert settings remain available for manual control.

> The current product name is temporary while the product and visual identity are still being finalized.

## What you can do

- **Create voiceovers** from a saved or newly recorded reference voice.
- Use **Auto** to choose a sensible model based on language and the compute backend actually available to the app.
- **Compare models** from one action. Compatible models are run safely one at a time so comparison also works on CPU-only or memory-constrained computers.
- Add exact digital pauses such as `[pause=0.5]` without asking the speech model to understand a pause token.
- Keep **projects, takes, voices, and generation history** locally.
- Use **Batch & subtitles** with TXT, Markdown, CSV, JSON, SRT, and VTT.
- Use the optional **Transcribe** tool for local speech-to-text.
- Install, load, update, unload, and remove speech models from a friendly **Models** page.
- Keep an installed model snapshot pinned until **you explicitly choose to update it**.
- Let **Performance: Auto** use CUDA, Apple MPS, or CPU when that backend is actually available to the installed runtime.
- Keep detailed sampling, preprocessing, verification, and diagnostics under **Settings → Expert** instead of the main creation screen.

## Current speech models

The user-facing names are intentionally simple:

| In the app | Current engine | Languages | Typical use |
|---|---|---:|---|
| **Multilingual** | Chatterbox Multilingual V3 | 23 | Arabic and multilingual work |
| **Expressive** | Chatterbox Turbo | English | expressive English and paralinguistic tags |
| **Light** | Chatterbox Nano | English | CPU-friendly local generation |

The architecture keeps model-management and product language separate so the interface does not have to become tied to one model family forever. The current implementation focuses only on these three models.

## Install for development / current local testing

### Windows 10 / 11

Requirements today:

- Python 3.11 64-bit
- Git (required by the pinned upstream dependency)

Run:

```powershell
.\scripts\setup_windows.bat
.\scripts\start_windows.bat
```

Then open:

```text
http://127.0.0.1:7860
```

### Linux

```bash
bash scripts/setup_linux.sh
bash scripts/start_linux.sh
```

The current scripts are the **developer/testing distribution path**. A consumer installer that bundles the required runtime is a separate packaging milestone; the application UI is being structured so normal users will not need to manage Python, Git, Hugging Face cache paths, or terminal commands.

## First use

1. Open **Create**.
2. Choose a saved voice, or press **+ Add a voice** and upload/record one.
3. Leave **Model = Auto** and **Language = Auto** unless you want to override them.
4. Write the script.
5. Press **Generate**.
6. Use **Compare models** when you want to hear the same compatible script across the available models.

The **Models** page shows what is installed and lets you install or intentionally update a model before generation.

## Speech to Text

Transcription is optional. From **Settings → Speech tools**, install the optional local speech dependencies once, then use **Tools → Transcribe**. The normal Create screen does not expose Whisper model names or similarity thresholds unless you intentionally open Expert settings.

Manual installation is still available:

```bash
python -m pip install -r requirements-optional.txt
```

## Local files

```text
data/
├── model_state.json   # exact model snapshots selected by the app
├── settings.json
├── voices/
└── projects/

outputs/
└── ...
```

Deleting a speech model from the Models page does **not** delete voices or projects.

## Privacy and local behavior

The normal app binds to `127.0.0.1` and Gradio analytics are disabled. Saved voices, projects, settings, generated WAVs, and metadata remain local. Network access is needed when installing or updating model/dependency files. **Offline mode** prevents missing models from being downloaded.

Only clone voices you have the right and consent to use.

## Open-source credits and independence

This is an independent community application. It is **not** an official Resemble AI product and is not endorsed by Resemble AI.

The current speech engines use the open-source **Chatterbox Multilingual V3, Turbo, and Nano** models/implementation by Resemble AI. Their upstream watermarking behavior is preserved. See [`NOTICE.md`](NOTICE.md), [`LICENSE`](LICENSE), and the pinned dependency in [`requirements.txt`](requirements.txt) for source and licensing details.

## Tests

Model-free tests:

```bash
python -m pip install -r requirements-dev.txt
pytest -q
```

CI also contains a full UI import/server smoke, real CPU generation smokes for Multilingual/Expressive/Light, and an optional Faster-Whisper helper smoke.
