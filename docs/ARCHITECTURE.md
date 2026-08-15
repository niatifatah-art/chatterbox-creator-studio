# Chatterbox Creator Studio architecture

Creator Studio is a local product and reliability layer around the open-source Chatterbox family. The central invariant is:

> **Assist the creator without silently taking control away from them.**

## Product invariants

1. **Local first** — generation, references, projects, settings, WAV files and metadata live on the user's machine.
2. **Raw mode always exists** — users can bypass Studio pause parsing, text preprocessing and automatic chunking.
3. **Transformations are explicit** — text/audio transformations are opt-in and visible.
4. **Model capabilities drive the UI** — controls unsupported by the chosen model should not pretend to work.
5. **The UI is not the model engine** — Gradio orchestrates reusable modules; model-specific inference stays in adapters.
6. **Reproducibility matters** — generations record model, seed, settings, source and actual generated chunks.
7. **Valid output is not thrown away casually** — convenience operations such as subtitle fitting may warn/fallback rather than discard a good raw generation.
8. **Upstream stays upstream** — prefer adapters and targeted workarounds over copying or forking Chatterbox internals without a demonstrated need.

## High-level flow

```text
                         ┌─────────────────────┐
                         │       app.py        │
                         │ Gradio product UI   │
                         └──────────┬──────────┘
                                    │
             ┌──────────────────────┼──────────────────────┐
             │                      │                      │
      ┌──────▼──────┐        ┌──────▼──────┐       ┌──────▼──────┐
      │ ProjectStore │        │ VoiceLibrary │       │ Batch parser │
      │ projects/take│        │ refs/analysis│       │ SRT/VTT/etc. │
      └──────┬──────┘        └─────────────┘       └──────┬──────┘
             │                                             │
             └──────────────────┬──────────────────────────┘
                                │
                     ┌──────────▼──────────┐
                     │ Reliability policy  │
                     │ preprocess/retry/QC │
                     │ optional STT verify │
                     └──────────┬──────────┘
                                │
                     ┌──────────▼──────────┐
                     │ ChatterboxEngine    │
                     │ pause/chunk/output  │
                     └──────────┬──────────┘
                                │
                     ┌──────────▼──────────┐
                     │   Model registry    │
                     │ V3 / Turbo / Nano   │
                     └──────────┬──────────┘
                                │
                     ┌──────────▼──────────┐
                     │ WAV + JSON metadata │
                     └─────────────────────┘
```

## Model registry

`studio/models.py` is the source of truth for model capabilities. Each model declares its languages and whether it supports CFG, exaggeration, Min P, Top K or native paralinguistic tags.

The UI consumes these declarations. Adding another Chatterbox-family model should normally mean adding an adapter and registry entry rather than scattering new conditionals throughout the UI.

## Engine

`studio/engine.py` owns model-independent synthesis orchestration:

- device selection;
- one active adapter at a time;
- deterministic/random seed handling;
- exact-pause composition;
- smart chunking;
- post speech-speed stretching;
- WAV serialization;
- base generation metadata.

It does **not** know about Gradio projects, batch tables or tabs.

## Raw mode vs Studio mode

### Studio mode

Studio mode may:

- parse `[pause=...]` markers outside the model;
- smart-chunk long text;
- insert configurable digital gaps between automatic chunks;
- use explicitly enabled preprocessing/reliability helpers;
- record the actual chunks sent to the model.

### Raw Chatterbox mode

Raw mode:

- sends the whole script to the selected Chatterbox adapter;
- does not parse Studio pauses;
- does not smart-chunk;
- disables Studio text preprocessing in the application policy;
- intentionally exposes upstream text/model behavior.

Output-only checks can still be enabled separately because they do not rewrite the model input.

## Text preprocessing

`studio/preprocess.py` contains small, composable, optional transformations. The pipeline returns both the original and processed text plus warnings. The UI exposes a preview before generation.

Number normalization is deliberately an optional dependency because language support varies and it is not required for basic TTS.

## Reliability layer

`studio/reliability.py` wraps the engine; it does not replace it.

A `GenerationPolicy` can request:

- preprocessing;
- local audio quality checks;
- automatic retry;
- Best-of-N generation;
- Faster-Whisper verification.

Candidates remain separate files. Selection is based on explicit local scores. Best-of-N uses the lightweight audio QC score when no other verifier was requested so “best” is never a meaningless label; this implicit ranking signal is recorded in metadata.

Faster-Whisper is lazy and cached after first use.

## Projects and Takes

`studio/projects.py` owns the JSON-backed project store. Project data does not depend on Gradio. Takes are copied into the project so a later generation cannot overwrite the selected history.

Project schema v1 includes room for future segment-level workflows without making segment editing a requirement for the current product.

## Voice Library

`studio/voices.py` owns reference files and advisory metadata. The analyzer intentionally warns rather than rejects. A creator can always keep using a reference even when the Studio considers it short, quiet or silence-heavy.

## Batch / subtitle pipeline

`studio/batch.py` is pure input parsing; `studio/batch_runner.py` performs generation. This separation allows TXT/CSV/JSON/SRT/VTT parsing to be tested without a model.

For timed subtitle cues, duration fitting is optional. If the required stretch exceeds the configured safety limit, the runner preserves the raw take and writes the warning into the batch manifest.

## Audio finishing

`studio/audio.py` owns local post-processing helpers. They are separate from Chatterbox inference so a user can distinguish model output from later waveform processing.

## Diagnostics and cache management

`studio/diagnostics.py` reports the environment without changing it. `studio/model_cache.py` is deliberately conservative: deletion is restricted to detected direct ResembleAI Chatterbox entries under the Hugging Face cache.

## CLI

`studio/cli.py` uses the same core modules as the UI. This keeps batch/generation logic reusable and provides a model-independent path for automation without creating a second TTS implementation.

## Metadata layers

Base engine metadata records:

- model ID/name;
- device;
- selected reference;
- language;
- actual seed;
- Raw/Studio mode;
- chunking configuration;
- generation parameters;
- original script;
- actual generated chunks.

Reliability orchestration adds its own namespaced `studio_reliability` block rather than rewriting the base schema. This keeps provenance clear.

## Testing strategy

The test suite is intentionally layered:

1. **Model-free core tests** for parsers, projects, voice metadata, settings, adapters and orchestration.
2. **UI smoke test** that imports the app, starts Gradio and probes the local HTTP server.
3. **Real model smoke matrix** for Multilingual V3, Turbo and Nano on Linux CPU.
4. Hardware-specific CUDA/MPS/Windows validation remains separate because Linux CPU CI cannot prove driver/platform behavior it does not run.

A real-model smoke exercises model loading, reference conditioning, inference, WAV/JSON output, exact Studio silence, Raw mode (Nano) and explicit unload.

## Dependency rule

Core Creator Studio should not gain a heavy dependency merely because an optional helper exists. Optional packages belong in `requirements-optional.txt` and code paths must degrade clearly when those packages are absent.
