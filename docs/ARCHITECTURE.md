# Voice Studio architecture

Status: **current architecture and migration map**.

Voice Studio is a local-first speech product. Its two most important architectural rules are:

> **The user works with voices and speech tasks. Engines, checkpoints and runtimes are replaceable implementation details.**

> **Voice Studio is one client of Speech Core. Any local project may consume the same small versioned protocol without importing the Studio UI.**

See also:

- `docs/product/voice-studio-product-spec.md` — product behavior and scope.
- `docs/adr/0001-speech-platform-boundaries.md` — accepted platform boundary.
- `docs/PROTOCOL.md` — public compatibility/RPC contract.
- `docs/ENGINE_SDK.md` — engine replacement/integration rules.
- `docs/ROADMAP.md` — staged completion plan and merge gates.

## Product invariants

1. **Local first.** Creator speech data stays local unless a future remote capability is explicitly selected.
2. **Voice is the durable identity.** A saved voice is addressed by stable `voice_profile_id`, never a provider/model/path tuple.
3. **Capabilities are public; engines are implementation.** `speech.synthesize.v1` remains the task even when the engine changes.
4. **No silent large downloads or upgrades.** Missing models are reported; explicit installation/update owns network acquisition.
5. **Established voices do not silently drift.** A pinned engine/model revision must remain available or generation fails visibly.
6. **Capability-driven UI.** Unsupported controls must not pretend to work.
7. **Reproducible artifacts.** Results retain engine/model/voice revision and safe provenance.
8. **No Core-private absolute paths in public artifacts.** `ArtifactRef` owns logical identity; the local resolver owns the path.
9. **No account/platform concepts in Speech Core.** Larger products own their own mapping to `voice_profile_id`.
10. **Migration before deletion.** Legacy stores/code disappear only after replacement parity is proven.

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
                    │ voices / artifacts    │
                    │ jobs / QA / engines   │
                    └──────┬─────────┬──────┘
                           │         │
                 ┌─────────▼─┐   ┌──▼──────────┐
                 │ TTS routes │   │ STT / tools │
                 └─────────┬─┘   └──┬──────────┘
                           │         │
             Chatterbox / future   Whisper / ...
             Kokoro / Qwen ...

Any other local project ── public Speech Client ──┘
```

The external boundary is deliberately ML-runtime-free. A caller should never need to import Gradio, PyTorch, Chatterbox, Qwen or a Studio screen.

## Layer responsibilities

### Voice Studio

Owns creator experience:

- Create / Voices / Projects / Transcribe / Engines / Settings;
- voice-first controls and semantic styles;
- project editing/take selection;
- explicit install/update actions;
- human-friendly progress/errors.

It must not gain new provider/model branches. The current Gradio shell is a migration client until the final desktop shell reaches parity.

### Speech protocol

`studio/protocol.py` is the dependency-light public vocabulary:

- version/compatibility constants;
- capability IDs;
- VoiceSource kinds;
- synthesis/transcription requests;
- artifact/timing/quality/provenance contracts;
- job states;
- stable semantic errors.

It imports no ML runtime and no product UI.

### Speech Core

Owns reusable speech behavior:

- capability/engine discovery;
- canonical Voice Profiles and bindings;
- routing policy;
- logical artifact lifecycle;
- synthesis/transcription/QA services as each becomes certified;
- runtime/model management boundaries.

**Current Phase 2 state:** Speech Core now executes the supported Chatterbox V3/Turbo/Nano synthesis family through `SpeechSynthesisService`. It resolves a Voice Profile, logical clone reference, route and exact selected model snapshot; returns `SpeechArtifact`; and exposes this over the stdio RPC/client. It never downloads a missing model during synthesis.

The current product UI still uses the direct legacy controller path until Phase 3 parity/cutover. This temporary duplication is intentional and must not become permanent.

### Engine adapters

An adapter translates semantic speech data into one engine's native behavior. Engine-native syntax/knobs stay here.

Examples:

- semantic event -> native tag or deterministic external behavior;
- Ready Voice -> native voice ID;
- Clone Reference -> conditioning artifact;
- semantic style -> tested engine recipe;
- sampling/tuning -> Advanced/internal execution settings.

During Phase 2 only the Chatterbox family has a Core execution adapter. Catalogued Qwen/Kokoro routes are rejected before the Chatterbox factory, preventing an implementation-family mix-up.

## Engine, runtime and model are different objects

Do not collapse:

```text
Engine route        chatterbox-v3
Runtime             chatterbox
Model asset          multilingual-v3 @ immutable resolved revision
Voice binding        VoiceProfile + calibrated route/model/recipe
```

This separation lets a model be replaced without changing the public capability, a runtime be upgraded without changing voice identity, and a new engine be added without product-UI branches.

## Engine status and Auto

Foundation statuses:

- `supported` — may currently be Auto-routed.
- `catalogued` — discoverable candidate, not allowed to displace a supported route before certification.

Routing currently uses compatibility, install state, explicit override, consistency pin and broad resource preference. Future `Best` ranking must consume certification measurements rather than marketing claims.

## Canonical Voice Profile

`VoiceProfile` is now the single durable identity source:

```text
VoiceProfile
├── profile ID + revision
├── source: ready / clone / designed / saved
├── canonical reference / native voice identity
├── supported languages
├── pronunciation + preferred styles
├── engine bindings
└── preferred engine consistency pin
```

`studio/voice_profile_store.py` persists this versioned shape. The old `studio/voices.py` library is only a compatibility facade/mirror for the current direct Gradio controller. It is removed after Phase 3 UI cutover, not before.

Promoting an engine binding may increment the voice revision and pin `preferred_engine_id`. If a binding pins an exact model revision and the selected local model has drifted, Core fails closed instead of silently changing the established voice.

## Artifacts

Portable contracts return logical artifacts:

```text
ArtifactRef
├── artifact_id
├── local://artifacts/... URI
├── MIME type
├── size
└── SHA-256
```

`ArtifactStore` maps the logical object to a local file. Resolution rejects traversal, ambiguous IDs, non-canonical aliases, mismatched ID/URI identity and hash tampering.

Generated Core speech is copied into artifact storage before temporary generation work is removed. External callers explicitly materialize an artifact to a path they own; public results do not expose the Core's private path.

## Local storage layout

Development runs keep the repository-local storage root; frozen builds use OS user data via `studio.paths.resolve_storage_root`.

```text
<storage root>/
├── data/
│   ├── voices/              # temporary legacy mirror for current UI
│   ├── projects/
│   ├── settings.json
│   ├── model_state.json
│   └── speech-core/
│       ├── voice-profiles/
│       ├── artifacts/
│       └── generation-work/ # temporary; successful work is cleaned
└── outputs/
```

External projects should use Speech Client/RPC and logical artifacts, not guess these paths.

## Storage decision

Do not introduce SQLite merely because later phases may need it. Current stores are small local versioned files with atomic replacement and migration backups. SQLite is reconsidered when durable concurrent jobs/history create an actual need.

Portable export remains JSON + media regardless of internal metadata storage.

## Local transport

Current reusable transport is JSON-RPC-style JSON lines over stdin/stdout. There is no default network listener.

Clients begin with `protocol.info` / `ensure_compatible()` and verify overlapping protocol/schema versions before higher-level calls. Clients branch on semantic error kinds, not English text.

Current public synthesis flow:

```text
external/Studio client
  -> SpeechSynthesisRequest
  -> Speech Core
  -> VoiceProfile
  -> route
  -> exact installed model snapshot
  -> Chatterbox engine
  -> logical SpeechArtifact
  -> optional explicit materialize(destination)
```

## Model downloads and cache

`LocalModelManager` reuses Hugging Face's shared cache and records the selected immutable snapshot revision. A moving upstream ref therefore does not automatically replace the selected model.

It is still Chatterbox-specific, especially its download specs/removal rules. Phase 4 generalizes Engine/Runtime/Model management before Kokoro/Qwen are allowed to rely on it. New engine families must not be bolted into ResembleAI-specific deletion logic.

## Runtime isolation target

Different engine families may require incompatible dependency stacks. Target:

```text
light product/core runtime
├── chatterbox runtime
├── kokoro runtime
├── qwen3-tts runtime
└── faster-whisper runtime
```

Runtime tooling may use `uv` or another reproducible manager, but this is an Engine Manager implementation choice, never a public API requirement.

## Testing gates

Tests are layered:

1. model-free Core/contract tests on Linux + Windows;
2. RPC subprocess/external-client tests;
3. browser UI E2E;
4. Windows product smoke;
5. optional-helper smoke;
6. real V3/Turbo/Nano model smokes when synthesis/runtime/model paths change;
7. migration/privacy/integrity tests;
8. later clean-install/offline/update/rollback tests.

The real model smoke now executes V3/Turbo/Nano through Speech Core and checks model revision/provenance, valid audio, exact digital pause, safe artifact metadata and progress. Nano also runs a direct-path migration parity guard for stable generation semantics.

## Current migration sequence

```text
contracts/discovery                     ✅
canonical Voice Profile data            ✅
Core Chatterbox synthesis               Phase 2 / green gate pending merge
UI adopts Speech Core                   Phase 3
remove duplicate direct synthesis       Phase 3
Generic Engine/Runtime/Model Manager     Phase 4
Kokoro                                  Phase 5
Qwen3-TTS                               Phase 6
STT / QA / certification / projects     later phases
final Voice-first desktop UI/release    later phases
```

Do not reverse this order by adding Qwen/Kokoro branches directly to `app.py` or `product_app.py`.

## Non-goals

Voice Studio does not own image generation, video editing, publishing accounts, research/source acquisition, music/SFX generation or a full DAW timeline. Other products may reuse generic contracts later, but shared infrastructure is extracted only when real projects need it.
