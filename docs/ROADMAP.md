# Voice Studio completion roadmap

Status labels:

- ✅ Done / merged and tested
- 🟡 In progress
- ⬜ Planned
- 🧪 Catalogued / experimental, not Auto-routable

The order is intentional. Do not jump to new engines or a visual rewrite before the foundation immediately below them is green.

## Phase 0 — Contracts, documentation and compatibility 🟡

Goal: remove architectural ambiguity before moving working generation.

Work:

- update the obsolete Chatterbox-only architecture document;
- centralize RPC/schema version constants;
- add protocol discovery/compatibility checks;
- add stable machine-readable error kinds;
- make engine manifests distinguish route/runtime/model assets;
- remove engine-name-specific routing heuristics;
- define one Speech Core storage subdirectory;
- document protocol and Engine SDK;
- add a minimal external-client integration smoke;
- extend privacy `.gitignore` rules for future Speech Core state.

Merge gate:

- model-free Linux + Windows tests green;
- RPC subprocess tests green;
- existing UI behavior unaffected;
- no engine promoted/demoted accidentally.

## Phase 1 — Canonical voice data + migrations ⬜

Goal: one source of truth for a voice before any new engine depends on it.

Current debt:

- `studio/voices.py` has the working legacy `VoiceLibrary`/reference metadata;
- `studio/voice_profile_store.py` has the durable Voice Profile/bindings design;
- minimal `EngineBinding` and richer `EngineVoiceBinding` overlap.

Work:

- define one canonical persisted Voice Profile record;
- consolidate binding shape;
- create a migration adapter for existing `data/voices/*.wav + *.json`;
- migration writes backup first, validates result, then commits;
- preserve existing voice names/references and UI behavior;
- add export/import foundation without bundling model weights;
- add corruption/old-schema tests.

Merge gate:

- old voice directory fixture migrates with no data loss;
- migration is idempotent;
- rollback/backup test passes;
- legacy UI can still list/use migrated voices.

## Phase 2 — Speech Core executes Chatterbox synthesis ⬜

Goal: prove the reusable boundary with the already-working engine before adding another engine.

Work:

- add Core synthesis service using `SpeechSynthesisRequest`;
- use logical `VoiceSource` rather than a mandatory `voice_path` public API;
- return `SpeechArtifact` with provenance and logical artifact reference;
- expose synthesis through the public client/RPC;
- preserve pause parsing, chunking, seed, Raw mode and cancellation behavior;
- run old path and Core path against parity fixtures;
- point current UI at Core only after parity.

Merge gate:

- V3/Turbo/Nano real CPU smokes green;
- output metadata/provenance parity verified;
- current Create/Batch/Compare workflows still work;
- old direct path remains available until the UI cutover is green.

## Phase 3 — Remove duplicate synthesis path ⬜

Goal: one generation implementation.

Work:

- move remaining controller-only synthesis helpers behind Core services;
- remove new engine branches from `app.py`/`product_app.py` by rule;
- delete the legacy direct engine path only after real-model + UI parity;
- keep compatibility adapters only for stored project/recipe data.

Merge gate:

- one synthesis service owns generation;
- CLI/UI/public client consume it;
- no regression in real model smoke matrix.

## Phase 4 — Generic Engine/Runtime/Model Manager ⬜

Goal: make engine replacement/addition safe and boring.

Work:

- generalize Chatterbox-specific `LocalModelManager` source/delete rules;
- engine runtime definitions separated from model assets;
- isolated runtimes where dependencies conflict;
- reuse shared Hugging Face cache/snapshots;
- resolved immutable model revisions persisted;
- preflight disk/hardware/license metadata;
- install/verify/retry/remove/update/rollback jobs;
- runtime and model versions independently visible in diagnostics.

Merge gate:

- app opens with zero engines/models;
- install/cancel/failure/repair paths tested;
- no full shared-cache deletion;
- old working model remains available during candidate update validation.

## Phase 5 — Kokoro integration 🧪

Goal: first architecture test with a Ready Voice engine that should not require clone-reference hacks.

Work:

- isolated/runtime-certified Kokoro route;
- Ready Voice catalogue and preview assets with clear rights/notices;
- lightweight/CPU measurements;
- semantic style support only where credible;
- Engine Manager install/remove flow.

Why first: if Kokoro needs a dummy `voice_path`, the VoiceSource/Core abstraction is still wrong.

Merge gate:

- Ready Voice synthesis works without reference audio;
- low-resource benchmark recorded;
- licensing/notices complete;
- route remains catalogued until certification passes.

## Phase 6 — Qwen3-TTS integration 🧪

Goal: prove Ready + Clone + Voice Design on one family.

Work:

- isolated Qwen runtime;
- exact candidate checkpoints chosen and licensed/certified;
- reusable clone conditioning/prompt cache where appropriate;
- Voice Design candidate workflow: generate candidates, preview, choose, save;
- natural-language style controls translated inside adapter;
- per-voice Golden Recipe bindings.

Merge gate:

- each claimed capability tested independently;
- no Arabic claim if the selected route does not support Arabic;
- Voice Design/Clone data survives app restart;
- no catalogued route enters Auto before certification.

## Phase 7 — STT foundation ⬜

Goal: local transcription useful to the Studio itself.

Work:

- migrate current faster-whisper helper behind `speech.transcribe.v1`;
- return `TranscriptArtifact` with segment/word timing where available;
- add caption export SRT/VTT;
- keep known script as caption text for TTS-generated audio;
- evaluate whisper.cpp only for a real lightweight/portable advantage;
- evaluate other ASR engines only against a benchmark gap.

Merge gate:

- external audio transcription works offline after install;
- word timing behavior tested;
- no ASR text silently overwrites known TTS script text.

## Phase 8 — Reference, speech and voice QA ⬜

Goal: make quality protection a product feature, not a hidden heuristic.

Work:

- reference inspector: duration/speech/silence/clipping/level/noise warnings;
- speech QA: expected text vs local ASR in quality modes;
- voice QA: optional speaker-similarity check for cloned voices;
- audio QA: clipping/silence/basic loudness sanity;
- warnings remain inspectable; destructive auto-fixes are avoided.

Merge gate:

- QA failures never erase a valid raw take;
- false claims such as using ASR to score "metallicness" are avoided;
- QA metadata is stored in `QualityReport`.

## Phase 9 — Certification Matrix + Golden Recipes ⬜

Goal: make Auto/Best evidence-based.

Work:

- benchmark corpus by language/task;
- latency / RTF / RAM / VRAM / install size;
- intelligibility/pronunciation;
- speaker similarity for clone routes;
- known failure/cancellation/update cases;
- per Voice + Engine Golden Recipe;
- semantic style overlays;
- model update candidate A/B/rollback workflow.

Merge gate:

- Auto ranking reads certification data rather than engine-name `if` statements;
- `Best` means measured context-specific evidence;
- established voice bindings do not change silently.

## Phase 10 — Projects, segments and take history ⬜

Goal: long narration without regenerating everything.

Work:

- explicit segment schema/migrations;
- multiple takes per segment;
- selected take + restore history;
- regenerate selection/paragraph;
- semantic pauses/events per segment;
- export/import Project bundle.

Merge gate:

- project survives close/reopen;
- one segment can regenerate without destroying other accepted takes;
- old v1 projects migrate safely.

## Phase 11 — Voice-first product UI ⬜

Goal: normal user sees the task, not ML plumbing.

Default Create:

```text
Voice   [ ... ]
Style   [ ... ]

Script
...

Language detected · Auto · Local        Generate
```

Work:

- Models/Engines removed from normal Create path;
- My Voices / Ready Voices / Clone / Create Voice in one voice workflow;
- Advanced drawer for manual engine/model/tuning;
- collapsible left navigation;
- Dark / Light / System;
- RTL-ready and keyboard/focus accessible;
- only supported capability controls appear;
- update website preview only after UI behavior is real.

Merge gate:

- browser E2E for normal path;
- keyboard-only path;
- RTL layout smoke;
- no silent download/update;
- Gradio legacy shell kept until replacement reaches parity.

## Phase 12 — Desktop/release hardening ⬜

Goal: leave the project in a state that can be ignored for months and reopened safely.

Work:

- final desktop shell/sidecar packaging;
- no Python/Git/terminal for normal user;
- clean install with zero model assets;
- offline installed-engine workflow;
- update signing and rollback strategy;
- third-party notices + SBOM;
- diagnostics bundle with no user speech/content;
- backup/export/import documented;
- release checklist and architecture handoff.

Merge gate:

- fresh Windows install;
- uninstall/reinstall does not delete creator data;
- corrupted/missing engine asset has a recovery path;
- external minimal client still passes;
- docs accurately describe the shipped architecture.

## Explicit non-goals for this completion pass

Do not add:

- image/video generation;
- research/source acquisition;
- music/SFX generation/mixing;
- publishing platforms/accounts;
- cloud accounts/billing;
- a plugin marketplace;
- a full nonlinear DAW timeline;
- engines without a distinct user benefit.
