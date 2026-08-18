# Engine / Runtime / Model management

Status: **Phase 4 management contract**.

This document exists to keep three independent concepts independent:

```text
Engine route        what capability can perform a task
Runtime             executable dependency environment
Model asset          checkpoint/files selected at an exact revision
```

A Voice Profile may bind to an engine/model/recipe revision, but the UI and external Speech Core clients must not manage Python environments or Hugging Face cache paths directly.

## Why this split exists

Adding a second model family must not turn the product into one shared Python environment with mutually incompatible Torch/Transformers/audio dependencies. Likewise, updating a checkpoint must not silently mutate a calibrated voice identity.

The management path is therefore:

```text
EngineManifest
    ├── runtime_id ──> RuntimeManifest
    └── model_ids  ──> ModelAssetManifest[]

EngineManager
    ├── RuntimeManager
    └── LocalModelManager
```

`EngineManager.status()` is read-only and performs no downloads/install operations.

## Runtime manifests

`studio.engine_assets.RUNTIME_MANIFESTS` owns runtime identity, install mode, Python compatibility, exact requirements once audited, upstream source revision and license metadata.

Runtime modes:

- `host_legacy`: compatibility only for the currently shipped Chatterbox environment. RuntimeManager will not mutate/remove the host environment.
- `isolated`: app-owned runtime directory for a model family that may have conflicting dependencies.

Cataloguing a runtime does **not** make it installable. If exact requirements have not been audited/frozen, `plan_install()` returns `can_install=false` rather than guessing.

The installer implementation prefers `uv` when it is already available/bundled and falls back to stdlib `venv` + pip in development. The public Speech protocol never exposes either tool.

A runtime state records a manifest fingerprint. If requirements/source metadata changes later, an old environment becomes `not ready` until it is repaired/recreated.

## Model asset manifests

`MODEL_ASSET_MANIFESTS` owns:

- stable Studio model ID;
- runtime ID;
- provider;
- upstream repository;
- upstream revision ref used for update discovery;
- allowed/expected files;
- weights license;
- optional size/notes.

The current provider implementation is Hugging Face Hub. The manager deliberately reuses the Hub cache instead of copying model snapshots into a second multi-GB store.

A downloaded snapshot is selected by its resolved immutable snapshot directory/revision, not by a moving `main` name.

## Selected / candidate / previous

Model state schema v2 has three roles:

```text
selected   known working revision used by generation
candidate  newly downloaded + verified revision, not active yet
previous   retained former selected revision for rollback
```

Update flow for the future Engine Manager UI:

```text
Check update
-> Stage candidate
-> Verify / smoke / certify
-> Promote
-> retain previous

If regression:
-> Rollback without network
```

The backward-compatible one-click `update()` performs stage + promotion because the current UI predates candidate review. New UI should expose the safer split.

## Verification and trust

A model may be physically present but not ready.

Verification checks the manifest's expected files/patterns. A managed Hugging Face snapshot is `source_trusted=true` only when it lives below the exact allowlisted repository cache snapshot directory.

An externally selected local folder may be inspected but is never deleted automatically by model removal. Future Advanced/manual trust policy must be explicit; Auto must not silently execute arbitrary checkpoint folders.

A broken selected model becomes `repairable` rather than being treated as healthy.

## Removal safety

Removing one Studio model selection must never mean deleting the entire Hugging Face cache.

For managed Hub assets, deletion targets only the exact selected revision. Candidate/previous revisions are not silently deleted as a side effect. User-owned external folders are never deleted by Runtime/Model Manager.

## Current manifests vs future engines

Chatterbox V3/Turbo/Nano have real runtime/model manifests because they are shipped and tested.

Kokoro and Qwen are still catalogued only. Their runtime entries intentionally contain no install requirements and their engine manifests intentionally contain no model asset IDs until their dedicated phases research and freeze the exact official runtime/checkpoint/voice assets and licenses.

This rule prevents a catalogue card from becoming an accidental product-support claim.

## Adding a new model family

Do not add provider branches to UI code. The safe order is:

1. Research official runtime/checkpoint/license sources.
2. Define/update a `RuntimeManifest`.
3. Define exact `ModelAssetManifest` entries.
4. Add engine route manifest(s) and capabilities.
5. Add an isolated execution adapter behind Speech Core.
6. Add model-free lifecycle tests.
7. Add real model smoke/certification.
8. Only then promote catalogue status / Auto eligibility.

If a new family needs a fake clone reference just to satisfy old Chatterbox assumptions, stop and fix the Core abstraction instead.
