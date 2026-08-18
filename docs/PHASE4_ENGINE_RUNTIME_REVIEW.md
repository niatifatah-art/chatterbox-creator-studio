# Phase 4 — Engine / Runtime / Model management review

Status: **implemented and gated on PR #14 before merge**.

This review is the Phase 4 handoff for the ordered completion plan. `docs/ENGINE_MANAGER.md` is the durable design reference.

## What changed

- Engine routes, runtime environments and model assets now have separate manifest types.
- Current Chatterbox V3/Turbo/Nano model sources are described by model manifests instead of being hard-wired into model-manager source/delete logic.
- Future Kokoro/Qwen runtime records are intentionally non-installable until their dedicated phases freeze exact official requirements/checkpoints.
- RuntimeManager manages app-owned isolated environments, manifest fingerprints, install plans, repair/remove, and refuses to mutate the legacy host runtime.
- LocalModelManager state schema v2 preserves the existing selected revision and adds candidate/previous revision roles.
- Model updates can be staged and verified without replacing the working selected revision, then explicitly promoted or rolled back without network access.
- Model verification detects missing/invalid expected assets and marks an installed selection repairable.
- Managed Hugging Face snapshots are distinguished from manually selected external folders; removal never deletes arbitrary user-owned folders.
- EngineManager is the product-facing read-only readiness/lifecycle service. Status queries do not trigger downloads or installs.
- Supported engine manifests must reference configured runtimes/model assets; catalogued engines may remain incomplete until their dedicated integration audit.

## Compatibility

The existing Chatterbox product remains compatible:

- the current runtime is explicitly `host_legacy` during this phase;
- existing setup/install flow continues to own that host environment;
- existing `LocalModelManager` class/method names remain available;
- `MODEL_DOWNLOAD_SPECS` remains as a compatibility view generated from model manifests;
- model state v1 is upgraded additively to v2 without changing the selected snapshot fields;
- the product still executes through Speech Core as established in Phase 3.

No Kokoro/Qwen execution code and no provider branches were added to the UI.

## Safety rules now enforced

1. A catalogued runtime with no audited requirements is not installable.
2. Runtime manifest drift invalidates readiness until repair/reinstall.
3. A model candidate is not active until promotion.
4. Promotion retains the former selection for rollback.
5. Repair is explicit and network-changing.
6. Remove targets only the exact managed provider revision; arbitrary external folders are never deleted.
7. Status/readiness calls are side-effect-free with respect to network/install operations.
8. Engine Auto eligibility requires a supported route plus ready runtime and healthy model state.

## Test gates

The Phase 4 branch adds tests for:

- engine/runtime/model manifest consistency;
- unsupported/catalogued runtime install refusal;
- isolated runtime fingerprint/readiness behavior;
- cleanup after failed runtime installation;
- isolated runtime removal / host-runtime refusal;
- model state v1 -> v2 migration;
- exact cached revision import;
- missing expected model files / repairable state;
- external model folder deletion safety;
- stage -> promote -> rollback lifecycle;
- candidate discard without selected-revision mutation;
- EngineManager Auto readiness without implicit install/download;
- product-core initialization without importing heavy/optional ML modules.

Existing Linux/Windows Core, UI E2E, Windows UI, optional-helper, and V3/Turbo/Nano real-model workflows remain merge gates.

## Deferred deliberately

- Moving Chatterbox itself from the host environment into an isolated release runtime. That requires packaging/runtime delivery work and should not break the currently green application merely to satisfy an abstraction aesthetically.
- Exact Kokoro runtime/checkpoint/voice manifests: Phase 5 researches and certifies them first.
- Exact Qwen runtime/checkpoint manifests: Phase 6.
- Faster-Whisper runtime migration behind Speech Core: STT phase.
- Final Engine Manager visual page: Voice-first UI phase; it consumes the service created here.

## Next safe phase

Phase 5: **Kokoro as the first non-Chatterbox TTS integration**.

The point of that phase is not to increase model count. It proves that Ready Voices + a lightweight local route can plug into the generic engine/runtime/model lifecycle without a clone-reference hack. If it cannot, the abstraction must be fixed before proceeding to Qwen.
