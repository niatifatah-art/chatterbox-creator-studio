# Speech Core protocol

Status: **public local integration contract**.

The protocol exists so Voice Studio, a CLI, ACE, or any other local project can use speech capabilities without importing UI code or engine implementations.

## Design rules

- Public APIs are capability-oriented, not provider-oriented.
- JSON payloads contain no private absolute machine paths.
- A caller discovers capabilities before assuming they exist.
- Version compatibility is checked before long-running work.
- Human messages may change; machine-readable error kinds do not change casually.
- The current transport is stdio JSON lines, but contract objects do not depend on that transport.
- Models and runtimes are implementation details unless a caller explicitly requests an advanced engine override.
- Missing models are reported; synthesis never starts a large download implicitly.
- Catalogued engines are discoverable but cannot execute through an unrelated adapter.

## Versions

The canonical constants live in `studio.protocol`:

- `RPC_PROTOCOL_VERSION`
- `MIN_RPC_PROTOCOL_VERSION`
- `SPEECH_SCHEMA_VERSION`
- `MIN_SPEECH_SCHEMA_VERSION`

A client should call `protocol.info` first. The local Python client exposes `ensure_compatible()`, which verifies that the server and client version ranges overlap.

Current protocol-info shape:

```json
{
  "rpc_protocol_version": 1,
  "min_rpc_protocol_version": 1,
  "speech_schema_version": 1,
  "min_speech_schema_version": 1,
  "transport": "stdio-jsonl",
  "schema_version": 1
}
```

Compatibility policy:

- adding an optional field is normally backward-compatible;
- adding a new capability or error kind is normally backward-compatible;
- renaming/removing a required field requires a schema-version change and migration period;
- changing JSON-RPC method semantics incompatibly requires an RPC protocol-version change;
- legacy versions are removed only after callers have an upgrade path.

## Transport

Current local transport:

```text
client process
   │ stdin/stdout
   ▼
Speech Core sidecar
```

Each line is one JSON-RPC 2.0 request or response. There is no default network listener.

The transport may later be adapted to a desktop sidecar or HTTP/WebSocket service, but request/artifact semantics should remain the same.

## Current methods

| Method | Purpose | Model load required |
|---|---|---|
| `health` | liveness + protocol summary | no |
| `protocol.info` | compatibility discovery | no |
| `capabilities.list` | known/supported capability routes | no |
| `engines.list` | engine/runtime/model catalogue metadata | no |
| `route.decide` | choose a compatible route | no |
| `voices.list` | list durable voice identities | no |
| `voices.get` | inspect one voice identity | no |
| `speech.synthesize` | synthesize a saved voice through Speech Core | yes |
| `artifacts.materialize` | copy a logical artifact to an explicit caller-owned path | no ML load |

Current executable synthesis routes are the supported Chatterbox V3/Turbo/Nano family. Other catalogued families remain discovery-only until their own adapters/runtimes are implemented and certified.

Planned methods are published only after their Core implementation is tested:

- `speech.transcribe`
- `speech.align`
- `speech.reference.inspect`
- `speech.quality.verify`
- `speech.speaker.verify`
- job submit/status/cancel methods if public operations become asynchronous.

Do not publish a method merely because its contract type exists.

## Capabilities

Capability IDs are semantic and versioned:

```text
speech.synthesize.v1
speech.transcribe.v1
speech.align.v1
speech.voice.clone.v1
speech.voice.design.v1
speech.voice.ready.v1
speech.reference.inspect.v1
speech.quality.verify.v1
speech.speaker.verify.v1
speech.vad.v1
speech.normalize_audio.v1
```

`capabilities.list` returns both all known engine implementations and the currently supported/Auto-routable subset. A client must not assume that a catalogued engine is installed, executable or certified.

## Engine discovery

`engines.list` exposes implementation metadata for management/debugging:

```json
{
  "engine_id": "chatterbox-v3",
  "family": "chatterbox",
  "runtime_id": "chatterbox",
  "model_ids": ["multilingual-v3"],
  "capabilities": ["speech.synthesize.v1", "speech.voice.clone.v1"],
  "languages": ["en", "ar"],
  "resource_tier": "heavy",
  "status": "supported"
}
```

`engine_id`, runtime and model are deliberately separate. A model checkpoint can be replaced behind the same public synthesis capability, while an established voice binding stays pinned until a replacement is explicitly promoted.

## Routing

A caller normally requests a capability plus constraints, for example:

```json
{
  "capability": "speech.synthesize.v1",
  "language": "ar",
  "needs_voice_clone": true,
  "priority": "auto",
  "installed_engines": ["chatterbox-v3"]
}
```

The router first filters incompatible engines. Catalogued engines cannot displace a supported route. Current ranking intentionally avoids engine-name-specific quality claims; future Best/Auto ranking consumes certification measurements.

Manual engine override remains possible for advanced callers, but it is validated against capability/language/source support. During the current migration, a catalogued non-Chatterbox route is rejected before any Chatterbox execution adapter is loaded.

## Voice identity

External callers reference `voice_profile_id`, not a WAV path/provider tuple.

Voice sources are semantic:

- `ready`
- `clone`
- `designed`
- `saved`

`VoiceProfile` is the canonical durable identity. It owns the source, revision, pronunciation/style metadata, engine bindings and an optional `preferred_engine_id` consistency pin. The legacy Gradio `VoiceLibrary` is only a compatibility facade while the UI is migrated.

Promoting an engine binding increments the voice revision and can make that engine the preferred consistency route. If a binding pins a model revision but a different model revision is currently selected, synthesis fails closed instead of silently changing the voice implementation.

## Synthesis request

The public request is semantic:

```json
{
  "text": "Hello",
  "voice_profile_id": "narrator",
  "language": "auto",
  "style": "auto",
  "priority": "auto",
  "engine_override": null,
  "schema_version": 1
}
```

Engine-native Chatterbox controls remain internal migration settings and are not made part of the stable public contract. Structured semantic events are rejected until their cross-engine execution semantics are implemented; they are never silently ignored.

The public Python client exposes:

```python
client.ensure_compatible()
client.capabilities()
client.voices()
result = client.synthesize(request)
client.materialize(result["audio"], destination)
```

A missing model returns a structured error. `speech.synthesize` does not download it.

## SpeechArtifact

Successful synthesis returns a portable artifact record containing the logical audio reference, duration, language, voice identity/revision, style, engine/model provenance and safe generation metadata. The legacy private metadata file may contain implementation details, but it is not exposed as the public artifact.

Public metadata intentionally excludes the original script and local filesystem paths. The request already owns the text; downstream callers should keep their own task context instead of relying on leaked engine metadata.

## Artifact references

Durable result contracts use logical artifacts:

```json
{
  "artifact_id": "art_123",
  "mime_type": "audio/wav",
  "uri": "local://artifacts/art_123",
  "size_bytes": 123456,
  "sha256": "..."
}
```

A local resolver owns the real path. External projects must not derive or persist the Studio's private filesystem path.

Artifact resolution verifies that the logical ID and URI identify the same canonical object and checks the hash when known. Ambiguous IDs, traversal, aliases and tampered artifacts are rejected.

`artifacts.materialize` is an explicit local export operation: the caller chooses the destination path. The result reports logical ID/status/size, not the Core's private storage path.

## Errors

JSON-RPC numeric codes remain useful for protocol tooling. Product clients should branch on `error.data.kind` from `SpeechErrorKind`.

Current semantic kinds include:

- `invalid_argument`
- `not_found`
- `no_compatible_engine`
- `protocol_incompatible`
- `engine_unavailable`
- `model_not_installed`
- `voice_reference_missing`
- `unsupported_voice_source`
- `generation_failed`
- `cancelled`
- `internal`

Example:

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "error": {
    "code": -32021,
    "message": "The model required by Multilingual is not installed.",
    "data": {
      "kind": "model_not_installed",
      "engine_id": "chatterbox-v3",
      "model_id": "multilingual-v3"
    }
  }
}
```

Clients must not parse English message text to decide recovery behavior.

## Privacy boundary

Public request/result metadata must not contain:

- social account names or handles;
- platform/publishing configuration;
- scripts/transcripts inside telemetry;
- Core-private absolute paths;
- secret tokens;
- hidden user identity defaults.

A larger orchestrator owns its own mapping from accounts/tasks to `voice_profile_id`.

## Minimal clients

`examples/speech_client_minimal.py` remains the model-free discovery/compatibility smoke. Synthesis callers use the same client after creating/importing a Voice Profile and explicitly installing the required engine/model. Keeping the discovery example model-free prevents integration tests from acquiring multi-GB ML dependencies merely to prove protocol compatibility.
