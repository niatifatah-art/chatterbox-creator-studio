# Chatterbox Creator Studio architecture

Creator Studio is a local product layer around the open-source Chatterbox family. The core rule is simple: **features may assist the user, but they must not silently take control away from them.**

## Design principles

1. **Local first** — generation, saved voices, settings, WAV files and metadata live on the user's machine.
2. **Raw mode always exists** — users can bypass Studio pause parsing and smart chunking and send text directly to the selected Chatterbox model.
3. **Transformations are explicit** — anything that changes text or audio must be visible and, where practical, optional.
4. **Model capabilities drive the UI** — do not show a control that the selected model ignores.
5. **The UI is not the engine** — Gradio should orchestrate reusable core modules, not contain model-specific inference logic.
6. **Reproducibility matters** — each generation records the model, seed, settings, script and chunks beside the WAV.
7. **Upstream stays upstream** — prefer adapters and targeted workarounds over copying or forking Chatterbox internals without a clear reason.

## v0.2 core

```text
app.py
  │
  ├── VoiceLibrary
  ├── SettingsStore
  └── ChatterboxEngine
          │
          ├── Model Registry
          │     ├── MultilingualV3Adapter
          │     ├── TurboAdapter
          │     └── NanoAdapter
          │
          ├── Text Pipeline
          │     ├── exact pause parsing
          │     └── multilingual smart chunking
          │
          └── Output
                ├── WAV
                └── JSON metadata sidecar
```

## Model registry

`studio/models.py` is the source of truth for model capabilities. Each model declares supported languages and whether it supports CFG, exaggeration, Min P, Top K, or native paralinguistic tags. The UI consumes those declarations instead of hard-coding model-specific branches everywhere.

Adding a future model should require a new adapter and registry entry, not a rewrite of the application.

## Raw mode vs Studio mode

### Studio mode

- parses `[pause=...]` markers outside the model;
- can smart-chunk long text;
- can insert a configurable digital gap between automatic chunks;
- records the actual chunks used for generation.

### Raw Chatterbox mode

- sends the whole script directly to the selected model;
- does not parse Studio pause tokens;
- does not smart-chunk the text;
- intentionally exposes upstream behavior, including upstream punctuation preprocessing.

This distinction is important: future verification, normalization, retry, denoise, or other reliability features must remain opt-in or clearly grouped into an assisted mode.

## Output metadata

Every generated WAV receives a same-name JSON file containing:

- schema version and timestamp;
- Chatterbox model ID/name;
- device label;
- selected voice file;
- language;
- seed;
- raw/studio mode;
- chunking settings;
- generation parameters;
- original script;
- exact chunks sent to the model.

This metadata is the base for future Takes, Projects, A/B comparison and exact regeneration.

## Next layers

The intended order after the v0.2 foundation is stable:

1. Projects and Takes.
2. Voice-library cards and reference diagnostics.
3. Batch/SRT/VTT workflows.
4. Optional reliability checks and retry.
5. Optional STT verification.
6. Optional text normalization and audio post-processing.
7. Model manager, diagnostics and packaging polish.

Those layers should depend on the core rather than being embedded into `app.py`.
