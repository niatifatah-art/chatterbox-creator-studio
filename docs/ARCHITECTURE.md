# Voice Studio architecture

Status: **current architecture and migration map**. This document replaces the original Chatterbox-only architecture description.

Voice Studio is a local-first speech product. Its most important architectural rule is:

> **The user works with voices and speech tasks. Engines, checkpoints and runtimes are replaceable implementation details.**

The second rule is equally important:

> **Voice Studio is one client of Speech Core, not the owner of Speech Core. Any local project may consume the same small versioned protocol without importing the Studio UI.**

See also:

- `docs/product/voice-studio-product-spec.md` — product behavior and scope.
- `docs/adr/0001-speech-platform-boundaries.md` — accepted platform boundary.
- `docs/PROTOCOL.md` — public compatibility and RPC contract.
- `docs/ENGINE_SDK.md` — how engines are added/replaced.
- `docs/ROADMAP.md` — staged completion plan and merge gates.

## Product invariants

1. **Local first.** Scripts, voices, projects, generated speech and metadata stay on the user's machine unless the user explicitly chooses a future remote capability.
2. **Voice is the primary identity.** A saved voice is represented by a stable `voice_profile_id`, not by a provider/model/path tuple.
3. **Capabilities are public; engines are private implementation choices.** `speech.synthesize.v1` remains stable while its engine may change.
4. **No silent large downloads or model upgrades.** Installed model revisions remain pinned until an explicit update.
5. **Capability-driven UI.** Unsupported controls do not pretend to work.
6. **Reproducibility.** Outputs retain engine/model/recipe revision and other relevant provenance.
7. **No absolute user paths in durable public artifacts.** `ArtifactRef` resolves paths inside the local implementation.
8. **No account/platform concepts in Speech Core.** A larger orchestrator owns its own account mapping.
9. **Raw output remains distinguishable from Studio transformations.** Finishing/preprocessing must not be mistaken for model behavior.
10. **Migration before deletion.** Legacy data/code is removed only after the replacement path has parity tests and a migration path.

## Durable architecture

```text
                    ┌───────────────────────┐
                    │      Voice Studio     │
                    │ UI / creator workflow │
                    └───────────┬───────────┘
                                │
                         public Speech Client
                                │
                    ┌───────────▼───────────┐
                    │      Speech Core      │
                    │ contracts / routing   │
                    │ voices / jobs / QA    │
                    │ artifacts / engines   │
                    └──────┬─────────┬──────┘
                           │         │
                 ┌─────────▼─┐   ┌──▼──────────┐
                 │ TTS routes │   │ STT / tools │
                 └─────────┬─┘   └──┬──────────┘
                           │         │
             Chatterbox / Qwen /   Whisper / ...
             Kokoro / future ...

Any other local project ── public Speech Client ──┘
```

The external boundary is deliberately model-free. A caller should never need to import Gradio, PyTorch, Chatterbox, Qwen or a Studio screen.

## Layer responsibilities

### Voice Studio

Owns the creator experience:

- Create / Voices / Projects / Transcribe / Engines / Settings screens;
- voice-first controls and semantic styles;
- project editing and take selection;
- explicit install/update actions;
- human-friendly errors and progress.

It must not contain new engine-specific provider branches. The current Gradio shell is a migration client until a future desktop shell reaches parity.

### Speech protocol

`studio/protocol.py` is the small dependency-light public vocabulary:

- version constants and compatibility information;
- capability IDs;
- voice source kinds;
- synthesis/transcription requests;
- artifact/timing/quality/provenance structures;
- job states;
- stable semantic error kinds.

The protocol imports no ML runtime and no product UI.

### Speech Core

Owns reusable speech behavior:

- capability discovery;
- voice profiles and calibrated bindings;
- routing policy;
- artifact lifecycle;
- job/progress semantics;
- reference/speech/voice quality gates;
- engine/runtime/model management.

The current JSON-RPC stdio server exposes discovery, routing and voice-profile access. Synthesis/transcription move behind this boundary in later completion phases only after parity tests.

### Engine adapters

An adapter translates public requests into one engine's native behavior. Model-specific syntax and knobs remain here.

Examples:

- semantic pause/event -> engine-native syntax or deterministic external silence;
- Ready Voice -> provider/engine voice identifier;
- Clone Reference -> conditioning asset;
- semantic `Creator` style -> engine-specific recipe;
- model-specific sampling controls -> Advanced only.

## Engine, runtime and model are different objects

Do not collapse these concepts:

```text
Engine route        chatterbox-v3
Runtime             chatterbox
Model asset          multilingual-v3 @ immutable resolved revision
Voice binding        voice profile + calibrated recipe for that route
```

`EngineManifest.runtime_id` groups routes that may share one isolated dependency environment. `model_ids` lists current model assets without making those model IDs part of the public capability contract.

This separation allows a checkpoint replacement without changing callers, a runtime upgrade without changing a voice identity, and an engine addition without adding UI conditionals.

## Engine status and Auto

Two statuses exist at the foundation:

- `supported` — currently allowed as an automatic route.
- `catalogued` — discoverable, but not eligible to displace a supported route until certification.

A future certification record supplies measured language/capability/quality/resource evidence. Until that exists, the router must not contain marketing-driven `if engine_id == ...` quality claims. Current routing uses compatibility, install state, consistency pinning and broad resource preference only.

## Stable voice identity

A Voice Profile is the long-lived object:

```text
Voice Profile
├── profile ID + revision
├── source: ready / clone / designed / saved
├── canonical reference or ready-voice identity
├── languages
├── pronunciation hints
└── calibrated engine bindings / Golden Recipes
```

The repository is currently in transition: `studio/voices.py` still serves the working Gradio controller while `studio/voice_profile_store.py` is the new durable identity store. They must be consolidated in the data phase; no third voice store may be introduced.

The current duplicated minimal/richer engine-binding shapes are also a migration item and must be reduced to one canonical persisted shape before new engines depend on them.

## Artifacts

Speech contracts return logical artifacts rather than leaking filesystem paths:

```text
ArtifactRef
├── artifact_id
├── local://... URI
├── MIME type
├── size
└── SHA-256
```

`ArtifactStore` maps the logical reference to a real file. A future remote resolver can map the same contract to object storage without changing the speech request/result vocabulary.

## Projects and takes

The existing `ProjectStore` remains JSON-backed during the migration and already reserves a `segments` field. The intended shape is logical segments with multiple takes:

```text
Project
└── Segment
    ├── text / voice / style / language / events
    ├── Take 1
    ├── Take 2
    └── selected take
```

A logical segment does not force one inference call; an adapter may preserve broader context while the product still supports paragraph/sentence regeneration.

## State storage decision for the current phase

Do **not** introduce SQLite merely because the product may need it later. Existing stores use small local files and atomic temp-file replacement, and moving all creator data at the same time as the Speech Core migration would increase failure surface without improving current synthesis parity.

Instead:

1. every persisted schema gets an explicit version;
2. migrations create a backup before rewriting user data;
3. storage access remains behind store classes;
4. durable job/history growth is measured;
5. SQLite is reconsidered when concurrent durable jobs/history actually justify it.

If adopted later, the database holds metadata/index/job state while creator audio remains ordinary files and portable export remains JSON + media.

## Local storage layout

Source/development runs keep the repository-local storage root; frozen builds use OS user data through `studio.paths.resolve_storage_root`.

Reusable Speech Core state has one canonical subdirectory:

```text
<storage root>/
├── data/
│   ├── voices/              # legacy working voice library during migration
│   ├── projects/
│   ├── settings.json
│   ├── model_state.json
│   └── speech-core/
│       ├── voice-profiles/
│       └── future core-owned state
└── outputs/
```

External local clients should either receive the Speech Core process from the application or pass `--data-dir` explicitly. They should not guess filesystem paths.

## Local transport and compatibility

The current reusable boundary uses JSON-RPC 2.0-style messages over stdin/stdout. JSON-RPC is transport-agnostic, so the request/response contract is not tied to a localhost HTTP server.

Every client should begin with `protocol.info` (or `ensure_compatible()` in `SpeechRpcClient`) and verify overlapping RPC/schema version ranges before using higher-level calls.

Stable semantic error kinds live in the speech protocol. Clients branch on those kinds, not human message text.

## Model downloads and cache

The current `LocalModelManager` uses Hugging Face's shared cache and records the exact selected snapshot so a moving upstream `main` ref does not silently change generation. This behavior is preserved.

The manager is still Chatterbox-specific in this migration phase, including download specs and conservative removal rules. It becomes a generic Engine/Model Manager only after the Speech Core synthesis path is stable. New engines must not be bolted into the existing ResembleAI-specific delete logic.

## Runtime isolation

Different engine families may require incompatible Python/Torch/Transformers stacks. The target is one lightweight product/core environment plus isolated engine runtimes where necessary.

Runtime creation/locking is an Engine Manager concern. A future runtime implementation may use `uv`, but the public speech contracts must not depend on that implementation choice.

## Testing strategy

Tests remain layered:

1. **model-free core/contract tests** on Linux and Windows;
2. **protocol subprocess tests** proving an external client can start and query Speech Core;
3. **UI browser tests** for creator behavior;
4. **platform smoke tests** for the desktop shell;
5. **real-engine smokes** only when engine/runtime/model behavior changes;
6. **migration/privacy tests** before legacy stores or schemas are changed;
7. **fresh install/offline/failure/rollback tests** as Engine Manager matures.

A phase is not merged simply because imports pass.

## Current transitional code

The working product still uses:

```text
product_app.py
→ app.py controller
→ VoiceLibrary / ProjectStore / LocalModelManager
→ ChatterboxEngine
→ V3 / Turbo / Nano
```

This path stays usable while the replacement is built beside it. The migration sequence is:

```text
contracts/discovery
→ canonical voice data
→ synthesis through Speech Core
→ parity comparison
→ UI adopts Speech Core
→ remove duplicate legacy path
→ add new engines
```

Do not reverse this order by adding Qwen/Kokoro branches directly to `app.py`.

## Non-goals

Voice Studio does not own image generation, video editing, publishing accounts, research/source acquisition, music/SFX generation or a full DAW timeline. Other products may consume the same generic artifact/job conventions later, but shared infrastructure is extracted only when at least two real products need it.
