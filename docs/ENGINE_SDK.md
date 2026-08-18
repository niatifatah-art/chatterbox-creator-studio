# Engine SDK and replacement rules

Status: **design contract for engine/runtime work**. The current Chatterbox adapters predate this document and are migrated incrementally; new engines must follow it.

The goal is not to support every speech model. The goal is to make a useful engine easy to add, replace, benchmark, disable or remove without rewriting Voice Studio or external clients.

## Terminology

- **Capability** — public task such as `speech.synthesize.v1`.
- **Engine route** — one implementation eligible for routing, e.g. `chatterbox-v3`.
- **Runtime** — dependency environment/process used by one or more engine routes, e.g. `chatterbox`.
- **Model asset** — weights/checkpoint/tokenizer files selected at an immutable resolved revision.
- **Voice binding** — calibrated way a Voice Profile uses one engine route.
- **Recipe** — tested engine-native tuning for a voice/style.

Do not use these terms interchangeably.

## Target package shape

Engine-specific implementation moves toward:

```text
engines/
  chatterbox/
    manifest
    adapter
    runtime
    recipes
    patches/
    tests/
    notices/

  kokoro/
  qwen3_tts/
  faster_whisper/
```

The exact Python module layout may evolve, but the ownership boundaries do not: upstream/model quirks stay under their engine family, not in the product UI or public protocol.

## Manifest requirements

Every route declares at minimum:

- stable `engine_id`;
- human display name;
- family;
- `runtime_id`;
- model asset IDs;
- capabilities;
- languages;
- resource tier;
- code license;
- weights/model license;
- status (`catalogued` or `supported` at the foundation);
- concise reason the route exists.

A route should not be added if its only reason is "another model exists".

## Why runtime and model are separate

Example:

```text
engine route: chatterbox-v3
runtime:      chatterbox
model:        multilingual-v3 @ commit abc123
```

This lets us:

- upgrade/fix Python dependencies without changing a Voice Profile identity;
- test a new checkpoint beside the old one;
- roll back a model revision after a quality regression;
- share one runtime between compatible variants;
- replace a model while public callers keep requesting the same capability.

## Model source policy

Model assets must have:

1. an allowlisted upstream repository/source;
2. an explicit requested revision/ref;
3. the immutable resolved revision recorded after download;
4. expected files/patterns;
5. license metadata separated from code license;
6. a verification/smoke step before becoming supported.

Prefer formats designed for safe tensor storage when upstream provides them. Never execute arbitrary downloaded model code merely because a model card suggests `trust_remote_code=True`; any such requirement needs explicit review and isolation.

## Cache policy

Reuse the upstream cache implementation rather than copying multi-gigabyte weights into a second private cache when possible.

For Hugging Face sources:

- reuse existing snapshots/blobs;
- pin the snapshot selected by the Studio;
- an upstream ref moving does not change the selected local revision;
- update downloads a new snapshot while the old working snapshot remains available until replacement is validated;
- deletion must be reference-aware and scoped to the exact selected asset.

The current `LocalModelManager` already follows part of this policy for Chatterbox. Its ResembleAI-specific deletion rules must be generalized before other engine families use it.

## Runtime isolation

New engine families must not force incompatible ML dependency stacks into one global environment.

Target behavior:

```text
product/core runtime
├── lightweight protocol/UI/core dependencies

engine runtimes
├── chatterbox
├── qwen3-tts
├── kokoro
└── faster-whisper
```

The runtime implementation may use `uv` or another reproducible environment manager. That choice must remain behind Engine Manager; callers never depend on it.

A runtime definition must be versioned/locked sufficiently to reproduce a certified installation.

## Adapter contract

An adapter translates public semantic data into native engine behavior. It must not require every voice to be a reference WAV.

The target input model is `VoiceSource`:

```text
ReadyVoice
CloneReference
DesignedVoice
SavedVoice
```

Therefore the current legacy adapter shape `generate(text, voice_path, options)` is a migration target, not the interface new engines should copy.

The adapter owns:

- native voice/prompt preparation;
- engine-specific sampling controls;
- language/native tokenizer requirements;
- native expressive tags;
- converting semantic style/events to supported native behavior;
- model-specific failure translation;
- engine-native cleanup/unload.

The adapter does **not** own:

- Gradio components;
- account/platform data;
- project UI state;
- public storage paths;
- Auto policy across engine families.

## Patches and upstream modifications

We may modify an open-source engine when that improves the product. Modifications must be traceable:

```text
upstream revision
+ adapter version
+ patchset version
+ recipe version
```

Prefer a small adapter/workaround first. If an upstream bug requires a patch, keep it isolated, documented and covered by a regression test. If the patchset becomes substantial, use a maintained fork with the original base revision recorded.

Do not paste upstream internals into generic Speech Core merely to avoid maintaining a proper engine boundary.

## Engine lifecycle

Engine Manager eventually owns:

```text
catalogued
→ preflight
→ runtime install
→ model download
→ verify
→ smoke
→ certify
→ supported
```

The normal user sees clear progress and can retry/remove. A large download never begins implicitly.

## Certification gate

A route cannot become `supported`/Auto-routable until it has evidence for its intended role:

1. code + weights + important dependency license review;
2. clean isolated install;
3. model load and smoke inference;
4. declared language/capability validation;
5. pronunciation/intelligibility checks appropriate to the capability;
6. speaker-similarity checks when cloning is claimed;
7. latency / real-time factor / RAM / VRAM measurements;
8. cancellation and failure behavior;
9. update/rollback behavior;
10. privacy/provenance output review.

The later Certification Matrix is data consumed by Auto. Do not hard-code "Qwen = Best" or similar claims inside the router.

## Why the initial engines exist

Current/candidate routes have distinct jobs:

- Chatterbox V3 — multilingual/Arabic cloning.
- Chatterbox Turbo — fast expressive English with native speech tags.
- Chatterbox Nano — lighter English cloning route.
- Kokoro — candidate ultra-light Ready Voice route.
- Qwen3-TTS — candidate Voice Design + Ready + Clone route.
- faster-whisper — current local transcription/timing route.
- whisper.cpp — candidate lightweight/portable ASR route.

An additional engine needs a comparably concrete reason.

## Adding or replacing a model

For a checkpoint replacement behind an existing route:

1. add the candidate asset/revision without deleting the current one;
2. smoke it in the existing runtime or a candidate runtime;
3. run the route's certification corpus;
4. compare quality, voice consistency and resource measurements;
5. keep existing Voice Profiles pinned to their current binding;
6. promote only after acceptance;
7. retain rollback until the replacement has proven stable.

No public capability or Studio screen should need modification merely because the checkpoint changed.

## Adding a new engine family

A new family is complete only when:

- manifest/catalog entry exists;
- isolated runtime definition exists;
- adapter implements the semantic source/capability it claims;
- install/remove path exists;
- tests pass without requiring UI-specific branches;
- licenses/notices are represented;
- certification status is explicit;
- at least one real product workflow benefits from it.

## Removal

Engines are replaceable. Removal should be possible without corrupting saved projects:

- existing artifacts remain playable;
- profiles keep provenance for historical takes;
- unavailable bindings are marked unavailable rather than erased silently;
- Auto chooses another compatible certified route only when that does not violate a consistency lock;
- the UI explains what must be installed/migrated.
