# ADR 0001 — Speech platform boundaries

Status: Accepted for the speech-platform foundation.

## Goal

Keep the local Voice Studio extremely simple for a normal creator while making its speech capabilities reusable by automation systems such as ACE and by future local or cloud clients.

## Decision

The product is split conceptually into four layers:

1. **Voice Studio** — user experience. It deals in voices, styles, scripts and projects. It does not expose model plumbing in the normal workflow.
2. **Speech Core** — routing, voice profiles, jobs, artifacts, quality gates and engine/runtime management.
3. **Engine adapters** — Chatterbox, Qwen, Kokoro, transcription engines and future providers. Engine-specific controls stay here.
4. **Speech protocol** — small versioned data contracts shared by any client. It imports no ML runtime and contains no account- or ACE-specific data.

ACE is a client of Speech Core. Voice Studio is another client. Neither imports the other.

```text
Voice Studio ─┐
              ├── Speech Core ── Engine adapters
ACE ──────────┘
```

## Stable account voices

An automation account binds to a `voice_profile_id`, not to a provider/model/checkpoint. A voice profile owns its revision, canonical source and calibrated engine bindings.

For automated publishing the default policy is **consistency first**: a new model or model update may be benchmarked, but it must not silently change an established account voice. Promotion to a new binding creates a new voice-profile revision.

## Capability contracts

Public capabilities use semantic, versioned IDs such as:

- `speech.synthesize.v1`
- `speech.transcribe.v1`
- `speech.align.v1`
- `speech.voice.clone.v1`
- `speech.voice.design.v1`
- `speech.reference.inspect.v1`
- `speech.quality.verify.v1`
- `speech.vad.v1`

A project can request one capability or compose several. Engine names are implementations, not contracts.

## Artifacts

Persisted contracts use `ArtifactRef` identifiers such as `local://artifacts/...` rather than absolute user paths. `SpeechArtifact` carries provenance, model/recipe revisions, timing and quality metadata so downstream clients do not need to infer them from a WAV filename.

When the source script is already known, captions keep that source text. STT/alignment contributes timing and verification rather than replacing known text with ASR output.

## Capability-driven UI

The UI shows a control only when the active route supports it. Examples:

- language is hidden for a fixed-language voice/engine;
- record/drop-zone is shown only for cloning;
- voice description appears only for voice design;
- engine-specific sliders remain under Advanced;
- user-facing styles are semantic (`Creator`, `Warm`, `Calm`) and are translated into engine-specific recipes.

## Engines and certification

Cataloguing an engine is not the same as shipping it. Before an engine becomes an automatic route it must pass:

1. source and weights license review;
2. isolated install/runtime test;
3. smoke generation/transcription;
4. language/capability checks;
5. quality and voice-consistency benchmark;
6. RAM/VRAM/latency measurement;
7. failure, cancellation and update tests.

Large model downloads are always explicit. Model revisions are pinned; updates never silently replace a working project route.

## Privacy

Speech Core contains no ACE account names, social handles, publishing platforms or private identity defaults. Analytics is off by default. If optional product analytics is enabled, events must contain operational metadata only and must never contain scripts, transcripts, recordings, voice references, absolute paths, account names or generated speech content.

## Migration rule

Existing Chatterbox UI/controller code remains supported while Speech Core is introduced behind it. New engines and cloud transport must target the protocol/Core boundary instead of adding more provider branches to the UI or ACE.
