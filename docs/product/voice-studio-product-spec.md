# Voice Studio product specification

Status: working product contract for the next implementation phases.

## Product rule

Voice Studio is a local-first speech workspace. The normal user deals with **voices, styles and text**. Models, checkpoints, runtimes and tuning are implementation details unless the user deliberately opens Advanced or Engines.

The durable architecture is:

```text
Voice Studio ─┐
              ├── Speech Core ── capability router ── engine adapters
ACE ──────────┘
```

Voice Studio never imports ACE. ACE never imports Voice Studio UI code. Both consume the same versioned Speech Core contracts.

## Normal Create experience

The default Create surface should remain intentionally small:

```text
Voice       [ Narrator ▾ ]
Style       [ Creator ▾ ]

Script
┌────────────────────────────────────┐
│ Write or paste what you want...   │
└────────────────────────────────────┘

Arabic · Auto · Local                      [ Generate ]
```

Rules:

- `Voice` is the primary choice.
- Language is Auto by default and is shown as a detected status when possible.
- Performance/quality is Auto by default.
- Engine/model selection is not part of the normal workflow.
- Unsupported controls do not appear.
- Advanced controls are available in a clearly discoverable drawer, closed by default.
- Large downloads always require explicit approval.
- No normal creator workflow requires Python, Git, Hugging Face or a terminal.

## Voice picker

A single picker owns the voice workflow:

```text
My voices
  Saved / cloned / designed voices

Ready voices
  Curated human-facing names

+ Clone a voice
Create a voice
```

`Clone a voice` shows upload/drag-and-drop/microphone recording only when a compatible engine exists or can be installed.

`Create a voice` shows a natural-language description field only when a compatible voice-design engine exists or can be installed.

Raw provider voice IDs remain internal or visible only in technical details.

## Stable voice identity

A Voice Profile is a durable identity, not a WAV filename and not a model choice.

A profile owns:

- stable profile ID and revision;
- ready/clone/designed source;
- supported languages;
- default semantic style;
- pronunciation hints;
- calibrated engine bindings;
- Golden Recipe per certified engine;
- optional per-style recipe overlays;
- model/recipe revisions and quality metadata.

For automation, **consistency first** is the default. A newly installed model must not silently replace an established account voice. Promoting a new engine binding creates a new voice-profile revision.

## Semantic styles

User-facing styles describe intent rather than model knobs. Initial vocabulary may include:

- Natural
- Creator
- Warm
- Calm
- Energetic
- Stable
- Expressive
- Angry when a route can deliver it reliably

Each engine translates a style using its own supported mechanism. A style is hidden when the selected voice/route cannot produce it credibly. Fake controls are not permitted.

Golden Recipes are calibrated per **voice + engine**, then semantic style overlays are applied above that baseline. This prevents one global default from degrading voices that need different tuning.

## Auto routing

Auto first filters incompatible routes, then ranks the remaining certified routes. Inputs include:

- requested capability;
- voice source type;
- language;
- style/features;
- consistency lock;
- installed state;
- device CPU/GPU/RAM/VRAM;
- requested priority: Auto, Best, Fast or Lightweight;
- benchmark/certification data.

`Best quality` is contextual, not a hard-coded model name.

For an established automation voice, a pinned certified binding wins unless the caller explicitly requests a migration/evaluation.

## Engine policy

Cataloguing an engine does not make it eligible for Auto. Every new engine/model revision must pass:

1. code, weights and important dependency license review;
2. isolated runtime/install test;
3. smoke inference;
4. capability and language validation;
5. quality and pronunciation benchmark;
6. voice-consistency benchmark when cloning is supported;
7. RAM/VRAM/latency measurement;
8. cancellation/update/failure tests.

Initial TTS families:

- Chatterbox Multilingual V3 — multilingual/Arabic cloning route.
- Chatterbox Turbo — expressive English route.
- Chatterbox Nano — lightweight English cloning route.
- Qwen3-TTS — catalogued for voice design, ready voices and cloning pending runtime certification.
- Kokoro — catalogued as an ultra-light ready-voice route pending integration certification.

Initial transcription families:

- faster-whisper — supported local transcription with word timings.
- whisper.cpp — catalogued portable/quantized transcription route.
- Fun-ASR multilingual — catalogued alternative multilingual route; timing behavior must be certified before caption routing.
- further ASR engines may be added only when they fill a real gap.

## Speech capabilities

Public capability IDs are small and composable. The current protocol includes synthesis, transcription, alignment, voice clone/design/ready voice, reference inspection, quality verification, speaker verification, VAD and audio normalization.

A future caller may request one capability or compose several. ACE is free to build a pipeline from the same nodes without exposing ACE concepts inside Speech Core.

## Captions and STT

When a caller already knows the script, the original script remains the text source. STT/alignment contributes timing and verification; ASR must not overwrite known text.

For external audio, STT produces the transcript and timing artifacts.

Speech QA may compare generated audio against expected text in quality-focused modes. Speaker verification may additionally protect long-lived cloned account voices from identity drift.

## Engine Manager

The Engines page is management, not the creative home screen. It may show technical names and licensing details.

Each engine card can expose:

- purpose / why install it;
- installed/available state;
- download size and storage use;
- supported capabilities/languages;
- hardware guidance;
- code and weights licenses;
- install/update/remove actions;
- exact installed revision;
- troubleshooting details.

Downloads should support clear progress, cancellation where safe, verification and retry. Model updates are explicit. Storage location should eventually be movable to another drive.

Runtime dependencies must be isolated by engine when required instead of forcing every ML backend into one fragile Python environment.

## Projects and segments

The simple editor may look like one text area, but projects are internally segment-based. Each segment can carry text, voice profile, style, language, pauses/events, result artifact and timings.

This enables regenerating one sentence without regenerating an entire long narration and gives ACE stable segment-level artifacts.

Pauses and expressive events are semantic protocol data. Engine-specific syntax such as a native laugh tag is an adapter concern.

## Local privacy

Default behavior:

- no product account required;
- no upload of scripts, transcripts, recordings or generated speech;
- analytics off by default;
- no autocapture or session replay;
- no absolute user paths in durable protocol artifacts;
- no ACE account names, handles, platforms or identity defaults in public Speech Core data;
- large network/model actions are explicit.

Optional analytics, when enabled, is limited to an allowlist of operational events and rejects content-bearing properties.

## ACE integration

ACE supports many independent creator accounts. ACE owns the mapping:

```text
ACE account A -> voice_profile_id X
ACE account B -> voice_profile_id Y
ACE account C -> voice_profile_id X
```

Speech Core does not know those account identities.

ACE should eventually replace direct provider branching with a small Speech Core client. Existing provider/voice fields remain a migration fallback until feature parity is proven. ACE narration normalization/mixing and later video stages remain separate concerns.

## UI system

The final desktop UI should use one coherent design language across future Studios while keeping this product speech-only.

Requirements:

- collapsible far-left sidebar;
- Dark / Light / System appearance;
- RTL-ready layout from the start;
- responsive sizing;
- short, restrained transitions;
- keyboard/focus accessibility;
- capability-driven controls;
- local/offline status that does not dominate the page;
- technical details behind Advanced/Engines.

Current Gradio remains a supported development/migration shell until a future desktop shell reaches feature parity. A visual rewrite must not force a Speech Core rewrite.

## Non-goals for this project phase

Do not add image generation, video editing, publishing-platform controls or ACE account management to Voice Studio. Those belong to other systems. Voice Studio should become excellent at speech first.

## Definition of done for each phase

A phase is not merged merely because code imports. Before merge:

- unit tests pass;
- relevant UI tests pass;
- platform smoke tests pass when affected;
- real-model tests run only when engine behavior changed;
- privacy/contract regression tests pass;
- existing creator workflows remain usable;
- no unsupported engine is silently promoted into Auto.
