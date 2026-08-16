# Chatterbox Creator Studio

A local-first creator workspace for open-source Chatterbox speech models. The product-facing name and visual identity are still temporary while the final brand is being designed.

The goal is simple: **say what you want to create; the app handles the technical path on your computer.** Normal use should not require knowing model IDs, Hugging Face cache paths, CUDA setup, sampling terminology, or terminal commands.

> Independent community project. This repository is not affiliated with or endorsed by Resemble AI. Current speech models come from the open-source Chatterbox project; attribution and licensing are documented in [NOTICE.md](NOTICE.md).

## Product experience

The supported product shell is `product_app.py`. It keeps the common path compact:

- Create with a saved or freshly recorded voice.
- Use `Model: Auto` or choose Multilingual, Expressive, or Light explicitly.
- Language controls disappear for English-only models.
- Missing models never download silently: Generate asks before a large download.
- Compare lets you choose exactly which compatible installed models to hear and streams results one at a time to protect memory.
- Save a result you like as a reusable sound recipe instead of remembering technical sliders.
- Transcribe audio locally with optional Faster-Whisper speech tools.
- Manage models with human-readable Download / Use / Update / Remove actions.
- Keep advanced controls under Settings → Expert.

The older `app.py` surface remains the stable controller/core while the product shell is being separated incrementally. This avoids rewriting proven generation, storage, and model-loading code in one risky step.

## Current models

| Product label | Upstream model | Languages | Best fit |
| --- | --- | --- | --- |
| Multilingual | Chatterbox Multilingual V3 | 23 | Arabic, multilingual work, high-quality general creation |
| Expressive | Chatterbox Turbo | English | Faster expressive speech and paralinguistic tags |
| Light | Chatterbox Nano | English | CPU-friendly local generation |

The app uses model-specific recommended tuning. Technical controls that a model does not use are hidden rather than shown as meaningless sliders. You can disable recommended tuning under Expert when you intentionally want manual control.

## Local data and privacy

- No account is required.
- Gradio analytics are disabled.
- Hugging Face telemetry is disabled by the desktop launcher.
- Voices, projects, recipes, generation history, and output audio are local.
- Network access is used for explicit model/dependency downloads and update checks.
- Offline mode blocks model downloads and update checks.
- Model updates are explicit; the app does not silently replace an installed model revision.

## Windows preview installer

A Windows desktop preview is built in GitHub Actions from `.github/workflows/windows-package.yml`.

The packaging path is intentionally conservative:

1. Build a one-folder PyInstaller application so missing dependencies are easier to diagnose.
2. Launch the bundled local server and perform an HTTP self-test.
3. Wrap the tested application in a normal Inno Setup installer.
4. Verify the installer artifact before uploading it.

The installer is currently versioned **1.1.1-preview** and installs for the current user. It creates a Start Menu shortcut and offers an optional desktop shortcut. Normal users do not need Python, Git, PowerShell, or a terminal to launch the packaged build.

Model weights are **not** bundled in the installer. Users choose which models to download from inside the app.

Until a formal GitHub Release is published, successful packaging workflow runs expose the installer as the `CreatorStudio-Windows-Preview` Actions artifact.

## Development setup

The project is developed against Python 3.11.

### Windows

```bat
scripts\setup_windows.bat
scripts\start_windows.bat
```

### Linux

```bash
./scripts/setup_linux.sh
./scripts/start_linux.sh
```

The product UI binds locally to `127.0.0.1` by default.

## Command line

The CLI remains available for development and automation:

```bash
python -m studio --help
python -m studio models
```

## Creator tools

The repository currently includes:

- exact digital pause tokens such as `[pause=0.5]`;
- Unicode-aware text handling and multilingual chunking;
- optional text preprocessing;
- projects and takes;
- voice upload / microphone capture / inspection / rename / duplicate / delete;
- TXT, Markdown, CSV, JSON, SRT and VTT batch input;
- subtitle timing-fit support;
- quality checks, retries and best-of-N generation;
- optional Faster-Whisper verification and transcription;
- audio finishing;
- generation history;
- hardware and environment diagnostics;
- explicit local model management;
- saved sound recipes;
- browser E2E validation for the primary product flows.

Starter voice packs are intentionally not bundled yet. Any future ready-made voices must have clear redistribution rights; random internet recordings will not be repackaged into the product.

## Validation

GitHub Actions cover:

- model-free tests on Ubuntu and Windows;
- Python compilation and CLI smoke tests;
- Chromium/Playwright browser E2E with screenshots;
- Windows product-shell import/start/HTTP smoke;
- real CPU generation with Multilingual V3, Turbo and Nano;
- optional Faster-Whisper / preprocessing / audio smoke;
- static website smoke;
- Windows desktop packaging and packaged-server self-test.

Real-model smoke coverage is currently CPU-based. Passing CI should not be interpreted as full certification of every NVIDIA/CUDA, Apple MPS, microphone, browser, or 23-language hardware combination.

## Website

The static landing page lives in `website/` and is Vercel-ready. It deliberately describes the product in human terms and keeps provider/model licensing details available without turning the main experience into a model dashboard.

The final public domain, product name, logo, application icon, and creator identity treatment are intentionally not locked yet.

## Upstream and licensing

The application layer in this repository is MIT licensed; see [LICENSE](LICENSE).

Current speech generation depends on the open-source Chatterbox project and its dependencies. See [NOTICE.md](NOTICE.md) for attribution and upstream source information. The project preserves the upstream watermarking behavior and does not include a watermark-removal feature.

Before redistributing a binary or adding third-party voice/model assets, review the applicable upstream and asset licenses rather than assuming the application-layer MIT license grants rights to every external artifact.
