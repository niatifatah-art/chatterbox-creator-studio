# Creator Studio

A calm, local workspace for creating and managing AI voiceovers.

The app follows one rule: **the common path stays simple, while technical control remains available when you intentionally look for it.** Choose a voice, write a script, and generate. Auto can choose a suitable model, language hint, and compute backend; deeper controls stay in Models and Settings → Expert.

> The current product name is temporary while the product and visual identity are still being finalized.

## What you can do

- **Create voiceovers** from a saved, uploaded, or newly recorded reference voice.
- Use **Auto** for sensible model, language, and compute choices.
- **Compare only the models you select.** The comparison UI shows which models are ready and never downloads a missing model just because you pressed Compare.
- Add exact digital pauses such as `[pause=0.5]`; the quick pause controls insert them at the current caret position.
- Keep **projects, takes, voices, and generation history** locally.
- Use **Batch & subtitles** with TXT, Markdown, CSV, JSON, SRT, and VTT.
- Use **Transcribe** for local speech-to-text; its optional speech tools can be installed from the Transcribe screen itself.
- Explicitly install, load, update, unload, and remove speech models from **Models**.
- Keep an installed model snapshot pinned until **you explicitly choose to update it**.
- Let **Performance: Auto** use CUDA, Apple MPS, or CPU when that backend is actually available to the installed runtime.
- Keep sampling, preprocessing, verification, and diagnostics under **Settings → Expert**.

## Current speech models

The user-facing names are intentionally simple:

| In the app | Current engine | Languages | Typical use |
|---|---|---:|---|
| **Multilingual** | Chatterbox Multilingual V3 | 23 | Arabic and multilingual work |
| **Expressive** | Chatterbox Turbo | English | expressive English and paralinguistic tags |
| **Light** | Chatterbox Nano | English | CPU-friendly local generation |

The product language and model-management architecture are kept separate so the interface does not have to be tied to one model family forever. The current implementation focuses only on these three models.

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

The start scripts launch `product_app.py`, the current product shell. `app.py` still contains the proven v1.1 controller and legacy component tree while the UI is being separated from the stable generation/storage core.

The scripts remain the **developer/testing distribution path**. A consumer installer that bundles the required runtime is a separate packaging milestone; normal users should eventually never need Python, Git, Hugging Face cache paths, or terminal commands.

## First use

1. Open **Create**.
2. Choose a saved voice, or press **+ Add a voice** and upload/record one.
3. Leave **Model = Auto** and **Language = Auto** unless you want an override.
4. Write the script.
5. Press **Generate**.
6. For comparison, tick the ready models you want and press **Compare selected**.

A missing model is not silently fetched by Generate, Batch, or Compare. Open **Models** and press **Install** when you actually want it downloaded.

## Speech to Text

Transcription is optional. Open **Tools → Transcribe**. If the local speech tools are missing, the same screen offers **Install speech tools**. Whisper model sizes and transcript-similarity controls remain under Expert unless you deliberately want them.

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

Deleting a speech model from Models does **not** delete voices or projects.

## Privacy and local behavior

The normal app binds to `127.0.0.1` and Gradio analytics are disabled. Saved voices, projects, settings, generated WAVs, and metadata remain local. Network access is used only for explicit model/dependency installs or update checks. **Offline mode** blocks model downloads and checks.

Only clone voices you have the right and consent to use.

## Open-source credits and independence

Creator Studio is an independent community application, not an official Resemble AI product. The current speech engines use the open-source Chatterbox model family and preserve the upstream watermarking behavior. Full attribution and license details live in [`NOTICE.md`](NOTICE.md), [`LICENSE`](LICENSE), and the pinned dependency in [`requirements.txt`](requirements.txt).

## Tests

Model-free tests:

```bash
python -m pip install -r requirements-dev.txt
pytest -q
```

CI also runs:

- Ubuntu and Windows core tests.
- A real product UI import/server smoke.
- Chromium end-to-end checks with Playwright for the primary interaction states.
- Real CPU generation smokes for Multilingual, Expressive, and Light.
- An optional Faster-Whisper helper smoke.

The browser workflow installs only Chromium for this validation pass. See `requirements-e2e.txt` and `.github/workflows/ui-e2e.yml`.
