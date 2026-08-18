# Speech Core protocol

Status: **public local integration contract**.

The protocol exists so Voice Studio, a CLI, ACE, or any other local project can use speech capabilities without importing UI code or engine implementations.

## Design rules

- Public APIs are capability-oriented, not provider-oriented.
- JSON payloads contain no absolute machine paths.
- A caller may discover capabilities before assuming they exist.
- Version compatibility is checked before long-running work.
- Human messages may change; machine-readable error kinds do not change casually.
- The current transport is stdio JSON lines, but contract objects do not depend on that transport.
- Model/runtimes are implementation details unless a caller explicitly requests a manual engine override.

## Versions

The canonical constants live in `studio.protocol`:

- `RPC_PROTOCOL_VERSION`
- `MIN_RPC_PROTOCOL_VERSION`
- `SPEECH_SCHEMA_VERSION`
- `MIN_SPEECH_SCHEMA_VERSION`

A client should call `protocol.info` first. The local Python client exposes `ensure_compatible()` which verifies that the server and client version ranges overlap.

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
| `capabilities.list` | available/supported capability routes | no |
| `engines.list` | engine/runtime/model catalogue metadata | no |
| `route.decide` | choose a compatible route | no |
| `voices.list` | list durable voice identities | no |
| `voices.get` | inspect one voice identity | no |

Planned methods are added only after their Core implementation is tested:

- `speech.synthesize`
- `speech.transcribe`
- `speech.align`
- `speech.reference.inspect`
- `speech.quality.verify`
- `speech.speaker.verify`
- job submit/status/cancel methods if operations become asynchronous over the public boundary.

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

`capabilities.list` returns both all known engine implementations and the currently supported/Auto-routable subset. A client should not assume that a catalogued engine is installed or certified.

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

`engine_id`, runtime and model are deliberately separate. A model checkpoint can be replaced while the external synthesis capability remains stable.

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

Manual engine override remains possible for advanced callers but is validated against capability and language support.

## Voice identity

External callers reference `voice_profile_id`, not a WAV path/provider tuple.

Voice sources are semantic:

- `ready`
- `clone`
- `designed`
- `saved`

The richer persisted voice-binding shape is being consolidated in the data phase. Until that migration is complete, callers should treat unknown optional binding fields as forward-compatible.

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

## Errors

JSON-RPC numeric codes remain useful for protocol tooling. Product clients should branch on `error.data.kind` from `SpeechErrorKind`.

Current semantic kinds:

- `invalid_argument`
- `not_found`
- `no_compatible_engine`
- `protocol_incompatible`
- `engine_unavailable`
- `internal`

Example:

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "error": {
    "code": -32004,
    "message": "Voice profile 'demo' was not found.",
    "data": {"kind": "not_found"}
  }
}
```

Clients must not parse English message text to decide recovery behavior.

## Privacy boundary

Public request/result metadata must not contain:

- social account names or handles;
- platform/publishing configuration;
- scripts/transcripts inside telemetry;
- local absolute paths;
- secret tokens;
- hidden user identity defaults.

A larger orchestrator owns its own mapping from accounts/tasks to `voice_profile_id`.

## Minimal client

See `examples/speech_client_minimal.py` for an executable, model-free integration smoke. It intentionally proves discovery/compatibility only until synthesis is migrated behind Speech Core.
