# Voice Studio completion roadmap

Status labels:

- ✅ Done / merged and tested
- 🟡 In progress / not merged yet
- ⬜ Planned
- 🧪 Catalogued / experimental, not Auto-routable

The order is intentional. Do not jump to new engines or a visual rewrite before the immediately preceding foundation is green.

## Phase 0 — Contracts, documentation and compatibility ✅

Delivered:

- Speech Core/public-client boundary;
- versioned RPC/schema discovery and compatibility checks;
- stable capability IDs and machine-readable errors;
- engine manifests separating route/runtime/model concepts;
- generic routing foundation;
- logical ArtifactRef boundary;
- model-free external-client smoke;
- architecture/protocol/Engine SDK documentation.

The current stdio boundary is intentionally transport-independent and has no default network listener.

## Phase 1 — Canonical voice data + migrations ✅

Delivered in PR #11:

- one canonical persisted `VoiceProfile` shape;
- one canonical `EngineBinding` contract with legacy compatibility alias;
- v1 -> v2 profile migration with backup before rewrite;
- legacy `VoiceLibrary` converted to a compatibility facade/mirror rather than a second identity source;
- Unicode/Arabic/CJK-safe local naming and Windows reserved-name handling;
- logical clone/reference artifacts with integrity checking;
- stable voice identity across display-name changes;
- non-destructive legacy voice migration;
- portable `.voicepack` export/import foundation with archive/hash/conflict/rollback validation.

The legacy `data/voices` mirror remains only because the current Gradio controller still needs a direct reference path. Phase 3 removes it after UI/Core parity.

## Phase 2 — Speech Core executes Chatterbox synthesis 🟡

Goal: prove the reusable boundary with the already-working engine before adding any new model family.

Implemented on PR #12:

- `SpeechSynthesisService` around `SpeechSynthesisRequest`;
- canonical Voice Profile + logical reference resolution;
- route selection and exact selected model-snapshot provenance;
- no implicit model download during synthesis;
- V3/Turbo/Nano execute through the existing proven Chatterbox engine behind Core;
- internal compatibility settings preserve seed, pauses, Raw mode, chunking, speed and current tuning without polluting the public semantic protocol;
- `SpeechArtifact` with logical audio ref and privacy-safe provenance/metadata;
- public `SpeechRpcClient.synthesize()` and explicit artifact materialization;
- preferred-engine consistency pin with voice revision promotion;
- fail-closed behavior if a pinned voice binding requires a different model revision;
- catalogued future engines cannot execute through the Chatterbox adapter accidentally;
- artifact identity canonicalization/integrity hardening;
- model-free synthesis/RPC/error/cancellation/raw-mode/revision-guard tests;
- real V3/Turbo/Nano Speech Core smoke matrix;
- Nano direct-path migration parity guard.

Current merge gate:

- Linux core ✅
- Windows core ✅
- browser UI E2E ✅
- Windows UI smoke ✅
- optional helper smoke ✅
- real V3/Turbo/Nano Speech Core smoke ✅
- docs reviewed/updated on this PR
- final diff/privacy/backwards-compatibility review required before merge

The UI is **not** cut over in this phase. The old direct path remains available until Phase 3 proves creator-workflow parity.

## Phase 3 — UI adopts Core; duplicate synthesis removed ⬜

Goal: one generation implementation.

Work:

- point current Create/Batch/Compare generation at Speech Core without changing expected user behavior;
- carry current technical controls through internal execution settings while the legacy UI exists;
- preserve model download consent, progress, stop/cancellation, project/history and output behavior;
- run old vs Core parity where useful;
- remove direct new-engine branches from UI/controller by rule;
- remove the duplicate direct synthesis path and temporary voice-file mirror only after parity is green;
- keep compatibility adapters only for stored project/recipe data.

Merge gate:

- one synthesis service owns generation;
- UI/CLI/public client consume it;
- V3/Turbo/Nano real model smokes remain green;
- Create/Batch/Compare browser workflows remain green;
- no data loss for existing voices/projects.

## Phase 4 — Generic Engine / Runtime / Model Manager ⬜

Goal: make engine replacement/addition safe and boring.

Work:

- generalize Chatterbox-specific `LocalModelManager` source/delete rules;
- separate engine route, runtime environment and model asset lifecycle;
- isolated runtimes where dependency families conflict;
- reuse shared Hugging Face cache/snapshots;
- select retained immutable revisions, including the exact revision required by a pinned voice binding;
- preflight disk/hardware/license metadata;
- explicit install/verify/retry/remove/update/repair/rollback jobs;
- runtime/model versions independently visible in diagnostics;
- app can open with zero heavy engines/models installed.

Merge gate:

- no full shared-cache deletion;
- old working model stays usable while a candidate update is tested;
- missing/failed/corrupt model has a clear recovery path;
- no large download occurs implicitly.

## Phase 5 — Kokoro integration 🧪

Goal: first non-Chatterbox architecture test with a Ready Voice/lightweight route.

Why first: if Kokoro needs a dummy clone reference, the VoiceSource/Core abstraction is wrong.

Work/gate:

- isolated runtime and exact model asset;
- Ready Voice catalogue/previews with notices/rights;
- CPU/resource benchmark;
- synthesis without reference audio;
- explicit Engine Manager install/remove flow;
- remain catalogued until license/install/quality certification passes.

## Phase 6 — Qwen3-TTS integration 🧪

Goal: prove Ready + Clone + Voice Design + supported style/instruction controls on one family.

Work/gate:

- isolated Qwen runtime;
- exact candidate checkpoints selected and audited;
- reusable clone conditioning where appropriate;
- Voice Design: describe -> candidates -> preview -> choose -> save;
- semantic controls translated inside adapter;
- no unsupported-language claim;
- no route enters Auto before certification.

## Phase 7 — STT foundation ⬜

Goal: local transcription useful to Voice Studio itself.

Work:

- move faster-whisper behind `speech.transcribe.v1`;
- return `TranscriptArtifact` with segment/word timings where available;
- SRT/VTT export;
- known original TTS script remains canonical caption text;
- add another ASR engine only for a measured distinct benefit such as lightweight portability.

## Phase 8 — Reference, speech and voice QA ⬜

Work:

- reference inspector: decode/speech/duration/silence/clipping/level/noise warnings;
- speech QA: expected text vs local ASR where useful;
- voice QA: optional speaker consistency for clone routes;
- audio QA: clipping/silence/loudness sanity;
- valid takes are retained with warnings rather than destroyed.

## Phase 9 — Certification Matrix + Golden Recipes ⬜

Goal: make Auto/Best evidence-based.

Measure by engine/model revision/language/capability/hardware tier:

- install/smoke success;
- latency/RTF/RAM/VRAM/install size;
- intelligibility/pronunciation;
- speaker similarity where relevant;
- failures/known issues.

Per Voice + Engine + Model Revision + Language + Style, store a tested Golden Recipe. New model revisions are candidates, never silent replacements.

## Phase 10 — Projects, segments and take history ⬜

Work:

- versioned segment schema;
- multiple takes per segment;
- selected take + restore history;
- regenerate paragraph/selection;
- semantic events per segment;
- portable Project bundle;
- safe migration of existing projects.

## Phase 11 — Voice-first product UI ⬜

Default Create target:

```text
Voice   [ ... ]
Style   [ ... ]

Script
...

Language detected · Auto · Local        Generate
```

Work:

- Models/Engines removed from normal Create path;
- My Voices / Ready Voices / Clone / Create Voice in one workflow;
- Advanced drawer for manual engine/model/tuning;
- collapsible navigation;
- Dark / Light / System;
- RTL + keyboard/focus accessibility;
- only supported controls appear;
- update website screenshots only after the UI behavior is real.

## Phase 12 — Desktop/release hardening ⬜

Goal: leave the project safe to ignore for months and reopen without archaeology.

Work/gate:

- final desktop shell/sidecar packaging;
- normal user needs no Python/Git/terminal;
- clean install with zero model assets;
- offline installed-engine workflow;
- signed update + rollback strategy;
- third-party notices + SBOM;
- privacy-safe diagnostics bundle;
- backup/export/import and release docs;
- fresh Windows install/uninstall/reinstall tests;
- external minimal client remains compatible.

## Explicit non-goals for this completion pass

Do not add:

- image/video generation;
- research/source acquisition;
- music/SFX generation or mixing;
- publishing platforms/accounts;
- cloud accounts/billing;
- plugin marketplace;
- full nonlinear DAW timeline;
- engines without a distinct user benefit.
