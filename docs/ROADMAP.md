# Voice Studio completion roadmap

Status labels:

- ✅ Done / merged or merge-ready with all required gates green
- 🟡 In progress / not yet fully gated
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

The legacy `data/voices` mirror remains only as a compatibility surface for the current Gradio-era name/path UI and old stored references. Canonical identity/reference ownership is already in Speech Core. The mirror is removed only when no shipped compatibility path still consumes it.

## Phase 2 — Speech Core executes Chatterbox synthesis ✅

Delivered in PR #12 and merged to `main`:

- `SpeechSynthesisService` around `SpeechSynthesisRequest`;
- canonical Voice Profile + logical reference resolution;
- route selection and exact selected model-snapshot provenance;
- no implicit model download during synthesis;
- V3/Turbo/Nano execute through the proven Chatterbox implementation behind Core;
- internal compatibility settings preserve seed, pauses, Raw mode, chunking, speed and current tuning without polluting the public semantic protocol;
- `SpeechArtifact` with logical audio ref and privacy-safe provenance/metadata;
- public `SpeechRpcClient.synthesize()` and explicit artifact materialization;
- preferred-engine consistency pin with voice revision promotion;
- fail-closed behavior if a pinned voice binding requires a different model revision;
- catalogued future engines cannot execute through the Chatterbox adapter accidentally;
- artifact identity canonicalization/integrity hardening;
- model-free synthesis/RPC/error/cancellation/raw-mode/revision-guard tests;
- real V3/Turbo/Nano Speech Core smoke matrix;
- Nano native-vs-Core migration parity guard.

All Phase 2 Linux/Windows/UI/real-model gates passed before merge.

## Phase 3 — Existing product generation adopts Speech Core ✅

Goal achieved in PR #13: one product synthesis owner without forcing the final visual redesign first.

Delivered:

- the former direct implementation is explicit `NativeChatterboxEngine`, used only behind Speech Core and in intentional parity tests;
- controller-facing `ChatterboxEngine(...)` is a temporary Core-backed compatibility facade, so existing Create/Batch/Compare/CLI call sites no longer own synthesis directly;
- saved legacy voice paths are resolved back to canonical Voice Profiles before generation;
- arbitrary CLI reference WAVs receive short-lived Core profile/artifact identities and are cleaned afterwards;
- one process-owned native engine is reused inside the facade so Best-of/retries/Batch/Compare preserve loaded-model performance;
- current technical controls flow through internal execution settings while the public speech contract stays semantic;
- current WAV/JSON history/output behavior is preserved through an internal migration callback while public `SpeechArtifact` remains privacy-safe;
- explicit legacy `set_model_path` now synchronizes the exact selected snapshot into Core model state;
- model manager gained an explicit `select_snapshot()` bridge for already-managed local revisions;
- reliability and Batch depend on a structural generation contract rather than the Chatterbox class;
- optional-helper smoke explicitly installs/selects Nano before synthesis, preserving the invariant that generation never starts a large download silently;
- explicit native Nano parity remains as a migration guard; normal product construction is Core-backed.

Merge gate passed on the final code head before documentation-only status updates:

- Linux model-free Core ✅
- Windows model-free Core ✅
- browser UI E2E ✅
- Windows UI smoke ✅
- optional Nano + Faster-Whisper helper smoke ✅
- real V3/Turbo/Nano Speech Core smoke ✅
- product output/history/seed/pause contracts preserved ✅
- no new provider branches added to UI/controller ✅

Temporary compatibility code remains only where the current Gradio-era interface still speaks in names/paths. It is not a second synthesis implementation and must not become an extension point for new engines.

## Phase 4 — Generic Engine / Runtime / Model Manager ⬜

Goal: make engine replacement/addition safe, explicit and boring before integrating Kokoro/Qwen.

Work:

- replace Chatterbox-specific model-source/download/removal tables with generic engine/runtime/model manifests;
- keep engine route, runtime environment and model asset lifecycle separate;
- support isolated runtimes where dependency families conflict;
- reuse shared Hugging Face cache/snapshots instead of duplicating multi-GB files;
- retain/select immutable revisions, including the exact revision required by a pinned Voice Profile;
- represent install state independently from catalog/certification state;
- add disk/hardware/license/source preflight metadata;
- explicit install/verify/retry/remove/update/repair/rollback operations;
- keep a working revision available while a candidate update is tested;
- expose runtime/model versions in diagnostics;
- prepare the product shell to open with zero heavy speech engines/models installed;
- reduce CI so UI/model-free jobs do not need the full ML stack when it is unnecessary.

Merge gate:

- no full shared-cache deletion;
- old working model stays usable while a candidate update is validated;
- missing/failed/corrupt model has a clear recovery path;
- runtime and model revisions remain independently inspectable;
- no large download occurs implicitly;
- existing Chatterbox V3/Turbo/Nano Core/product smokes remain green.

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
