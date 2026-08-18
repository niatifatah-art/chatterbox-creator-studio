# Creator Studio

A calm, local workspace for creating and managing AI voiceovers.

The app follows one rule: **the common path stays simple, while technical control remains available when you intentionally look for it.** Choose a voice, write a script, and generate. Auto can choose a suitable model, language hint, and compute backend; deeper controls stay in Models and Settings → Expert.

> The current product name and icon are temporary while the final identity is still being designed.

## What you can do

- **Create voiceovers** from a saved, uploaded, or newly recorded reference voice.
- Use **Auto** for sensible model, language, and compute choices.
- Hide irrelevant controls automatically: explicit English-only models do not show a redundant language selector.
- **Compare only the installed models you select.** Results arrive model-by-model and the comparison can be stopped.
- Stop a long normal generation from the Create screen instead of waiting for a CPU run you no longer want.
- Approve a missing model with **Download & generate** instead of getting a surprise multi-gigabyte download.
- Browse a friendly **Models library** with search, Installed/Available filters, and card-level Download / Use / Update / Remove actions.
- Use model-specific recommended tuning: Multilingual, Expressive, and Light do not pretend that one technical recipe is ideal for every engine.
- Add exact digital pauses such as `[pause=0.5]` at the current text caret; supported expression tags are shown only for models that use them.
- **Save a sound you like** as a local recipe containing the voice, model, style, exact tuning, actual seed, speed, and finishing choices, then restore that recipe without recommended defaults silently overwriting it.
- Keep **projects, takes, voices, saved sound recipes, and generation history** locally.
- Use **Batch & subtitles** with TXT, Markdown, CSV, JSON, SRT, and VTT; batch generation resolves the selected model's own recommended profile rather than inheriting unrelated tuning from Create.
- Use **Transcribe** for local speech-to-text; optional speech tools can be installed from the Transcribe screen itself.
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

The product language and model-management architecture are kept separate so the interface does not have to be tied to one model family forever. The current working implementation intentionally focuses only on these three models; cataloguing another engine does not make it an automatic or shipped route.

## Architecture and future integrations

The durable direction is deliberately different from a model-specific UI:

```text
Voice Studio ─┐
              ├── Speech Core ── capability router ── engine adapters
Other client ─┘
```

Voice Studio is a client of the reusable Speech Core. A future local project can use the same public client/protocol without importing Gradio or any model implementation. Public callers ask for versioned capabilities such as `speech.synthesize.v1`; engine, runtime and checkpoint choices remain replaceable implementation details unless an advanced caller explicitly overrides them.

The working Gradio/Chatterbox generation path remains in place while this boundary is migrated and tested. New engines are not added directly to `app.py` during that migration.

Architecture and implementation contracts:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — current architecture and migration map.
- [`docs/PROTOCOL.md`](docs/PROTOCOL.md) — local public compatibility/RPC contract.
- [`docs/ENGINE_SDK.md`](docs/ENGINE_SDK.md) — engine/runtime/model addition and replacement rules.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — ordered completion phases and merge gates.
- [`docs/product/voice-studio-product-spec.md`](docs/product/voice-studio-product-spec.md) — product behavior and UX contract.

A model-free external-client smoke lives in [`examples/speech_client_minimal.py`](examples/speech_client_minimal.py). Actual synthesis moves behind Speech Core only after the existing Chatterbox path reaches parity through that boundary.

## Install for development / current local testing

### Windows 10 / 11

Requirements for the development path:

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

The start scripts launch `product_app.py`, the current product shell. `app.py` still contains the proven v1.1 controller and legacy component tree while the UI and reusable Speech Core are separated from the stable generation/storage path.

## Windows desktop packaging preview

A separate packaging pipeline now builds the product as a normal Windows desktop application instead of asking end users to install Python or launch a terminal.

The preview path is:

```text
desktop_launcher.py
→ native pywebview window
→ local product server on 127.0.0.1
→ PyInstaller one-folder bundle
→ Inno Setup installer
→ CreatorStudio-Setup.exe
```

Model weights are **not** bundled into the installer. The installed app keeps the same explicit model-library behavior and downloads only models the user chooses.

The current installer metadata is **1.1.1-preview**. The GitHub Actions workflow `.github/workflows/windows-package.yml` builds the Windows bundle, starts the packaged executable in a self-test mode, probes its local HTTP server, builds the installer, verifies the artifact, and uploads a short-lived preview artifact. The final public signing, product name, and custom icon remain release/branding milestones rather than being faked in advance.

## First use

1. Open **Create**.
2. Choose a saved voice, or press **+ Add a voice** and upload/record one.
3. Leave **Model = Auto** and **Language = Auto** unless you want an override.
4. Write the script.
5. Press **Generate**. A Stop action appears while generation is active.
6. If the needed model is missing, review the model and approximate download size, then choose **Download & generate** or **Cancel**.
7. Open **Compare voices** only when you want comparison, select the installed models you actually want to hear, and press **Compare**.
8. When a result is exactly what you wanted, press **♡ Save sound** and give the recipe a human name.

Generate never begins a missing-model download until you approve it. Batch and Compare never download missing speech models implicitly.

## Speech to Text

Transcription is optional. Open **Tools → Transcribe**. If the local speech tools are missing, the same screen offers **Install speech tools**. Whisper model sizes and transcript-similarity controls remain under Expert unless you deliberately want them.

Manual installation remains available for development:

```bash
python -m pip install -r requirements-optional.txt
```

## Local files

Source/development runs preserve the existing repository-local layout:

```text
data/
├── model_state.json   # exact model snapshots selected by the app
├── settings.json
├── recipes.json       # reusable saved-sound recipes
├── voices/            # working legacy reference library during migration
├── projects/
└── speech-core/       # reusable Core-owned state as the migration progresses

outputs/
└── ...
```

Frozen desktop builds deliberately keep creator-owned data **outside the installed application bundle**. On Windows the default writable root is:

```text
%LOCALAPPDATA%\CreatorStudio\
├── data\
└── outputs\
```

That separation prevents a normal application update or uninstall from treating voices, projects, recipes, settings, and generated audio as bundled program files. Advanced/portable setups can override the writable root with `CREATOR_STUDIO_STORAGE_ROOT`.

Deleting a speech model from Models does **not** delete voices, projects, or saved sound recipes.

## Starter voices

The voice library is prepared for future starter packs, but this repository deliberately does **not** redistribute random demo/reference recordings. A starter voice will only be bundled or offered when its redistribution and intended use rights are clear enough for the project to ship it responsibly.

## Website

The static product site lives in [`website/`](website/). It is intentionally lightweight: HTML, CSS, and a small progressive-enhancement script. The landing page follows the same product language as the app, clearly labels the current name as temporary, and does not pretend that the consumer installer is publicly released before the packaging pipeline is certified.

`website/vercel.json` contains the static deployment configuration and basic response headers. The site has its own GitHub Actions smoke test before it is connected to a production Vercel project.

## Privacy and local behavior

The normal app binds to `127.0.0.1` and Gradio analytics are disabled. Saved voices, projects, recipes, settings, generated WAVs, and metadata remain local. Network access is used for explicit model/dependency installs and update checks. **Offline mode** blocks model downloads and checks.

Speech Core's public artifacts use logical local references rather than absolute user paths. Optional product telemetry is off by default and its allowlist rejects scripts, transcripts, voice paths and account identity fields.

Only clone voices you have the right and consent to use.

## Open-source credits and independence

Creator Studio is an independent community application, not an official Resemble AI product. The current speech engines use the open-source Chatterbox model family and preserve the upstream watermarking behavior. Full attribution and license details live in [`NOTICE.md`](NOTICE.md), [`LICENSE`](LICENSE), and the pinned dependency in [`requirements.txt`](requirements.txt).

The normal creation surface intentionally keeps these credits out of the way; they remain available in About, the repository notices, and the website footer rather than being repeated throughout the product.

## Tests

Model-free tests:

```bash
python -m pip install -r requirements-dev.txt
pytest -q
```

CI also runs:

- Ubuntu and Windows core tests.
- Model-specific profile, language-routing, writable-path, and saved-recipe tests.
- A model-free subprocess test proving an external client can negotiate and query Speech Core.
- A Windows product UI import/start/HTTP smoke.
- Chromium end-to-end checks for adaptive language controls, explicit download confirmation, caret pause insertion, Compare selection, the model library, and Transcribe setup.
- Real CPU generation smokes for Multilingual, Expressive, and Light when engine behavior changes.
- An optional Faster-Whisper / preprocessing / audio smoke.
- A static website smoke.
- A Windows desktop-package build and packaged executable self-test before producing an installer artifact.

Browser validation captures product screenshots for visual review as CI artifacts.
