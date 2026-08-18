from __future__ import annotations

import json
import shutil
import sys
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, TextIO

from studio.artifact_store import ArtifactStore
from studio.engine_registry import ENGINE_MANIFESTS
from studio.protocol import (
    ArtifactRef,
    Capability,
    EngineStatus,
    Priority,
    ProtocolInfo,
    SCHEMA_VERSION,
    SpeechErrorKind,
    SpeechEvent,
    SpeechSynthesisRequest,
)
from studio.speech_router import RouteError, RouteRequest, route
from studio.synthesis import SpeechSynthesisService, SynthesisError
from studio.voice_profile_store import VoiceProfileStore


JSONRPC_VERSION = "2.0"


class RpcError(Exception):
    def __init__(
        self,
        code: int,
        message: str,
        *,
        kind: SpeechErrorKind = SpeechErrorKind.INTERNAL,
        data: Any | None = None,
    ):
        super().__init__(message)
        self.code = int(code)
        self.message = str(message)
        self.kind = kind
        self.data = data


@dataclass(frozen=True, slots=True)
class RpcContext:
    data_dir: Path

    @property
    def voice_profiles(self) -> VoiceProfileStore:
        return VoiceProfileStore(self.data_dir / "voice-profiles")

    @property
    def artifacts(self) -> ArtifactStore:
        return ArtifactStore(self.data_dir / "artifacts")

    @property
    def synthesis(self) -> SpeechSynthesisService:
        return SpeechSynthesisService(
            self.data_dir,
            profile_store=self.voice_profiles,
            artifact_store=self.artifacts,
        )


def _clean(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_clean(item) for item in value]
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if hasattr(value, "to_dict"):
        return _clean(value.to_dict())
    if hasattr(value, "__dataclass_fields__"):
        return _clean(asdict(value))
    return value


def _require_dict(params: Any) -> dict[str, Any]:
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise RpcError(-32602, "Params must be a JSON object.", kind=SpeechErrorKind.INVALID_ARGUMENT)
    return params


def _protocol_info(_context: RpcContext, _params: dict[str, Any]) -> dict[str, Any]:
    return ProtocolInfo().to_dict()


def _health(_context: RpcContext, _params: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", **ProtocolInfo().to_dict()}


def _capabilities(_context: RpcContext, _params: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for capability in Capability:
        manifests = [manifest for manifest in ENGINE_MANIFESTS.values() if capability in manifest.capabilities]
        rows.append(
            {
                "id": capability.value,
                "engines": sorted(manifest.engine_id for manifest in manifests),
                "supported_engines": sorted(
                    manifest.engine_id for manifest in manifests if manifest.status == EngineStatus.SUPPORTED
                ),
            }
        )
    return rows


def _engines(_context: RpcContext, params: dict[str, Any]) -> list[dict[str, Any]]:
    include_catalogued = bool(params.get("include_catalogued", True))
    rows: list[dict[str, Any]] = []
    for manifest in sorted(ENGINE_MANIFESTS.values(), key=lambda item: item.engine_id):
        if not include_catalogued and manifest.status != EngineStatus.SUPPORTED:
            continue
        rows.append(
            {
                "engine_id": manifest.engine_id,
                "display_name": manifest.display_name,
                "family": manifest.family,
                "runtime_id": manifest.runtime_id,
                "model_ids": list(manifest.model_ids),
                "capabilities": sorted(item.value for item in manifest.capabilities),
                "languages": list(manifest.languages),
                "resource_tier": manifest.resource_tier,
                "status": manifest.status.value,
                "code_license": manifest.code_license,
                "weights_license": manifest.weights_license,
                "notes": manifest.notes,
            }
        )
    return rows


def _route(_context: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    try:
        capability = Capability(str(params["capability"]))
    except KeyError as exc:
        raise RpcError(-32602, "Missing required param: capability", kind=SpeechErrorKind.INVALID_ARGUMENT) from exc
    except ValueError as exc:
        raise RpcError(-32602, "Unknown speech capability.", kind=SpeechErrorKind.INVALID_ARGUMENT) from exc

    try:
        priority = Priority(str(params.get("priority", Priority.AUTO.value)))
    except ValueError as exc:
        raise RpcError(-32602, "Unknown routing priority.", kind=SpeechErrorKind.INVALID_ARGUMENT) from exc

    installed = params.get("installed_engines") or []
    if not isinstance(installed, list) or not all(isinstance(item, str) for item in installed):
        raise RpcError(-32602, "installed_engines must be a list of engine IDs.", kind=SpeechErrorKind.INVALID_ARGUMENT)

    request = RouteRequest(
        capability=capability,
        language=str(params.get("language") or "en"),
        priority=priority,
        needs_voice_clone=bool(params.get("needs_voice_clone", False)),
        needs_voice_design=bool(params.get("needs_voice_design", False)),
        needs_ready_voice=bool(params.get("needs_ready_voice", False)),
        weak_cpu=bool(params.get("weak_cpu", False)),
        installed_engines=frozenset(installed),
        consistency_engine=str(params["consistency_engine"]) if params.get("consistency_engine") else None,
        engine_override=str(params["engine_override"]) if params.get("engine_override") else None,
    )
    try:
        decision = route(request)
    except RouteError as exc:
        numeric_code = -32010 if exc.kind == SpeechErrorKind.NO_COMPATIBLE_ENGINE else -32602
        raise RpcError(numeric_code, str(exc), kind=exc.kind) from exc
    return _clean(decision)


def _voices_list(context: RpcContext, _params: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for profile_id in context.voice_profiles.list_ids():
        record = context.voice_profiles.get(profile_id)
        if record is None:
            continue
        rows.append(
            {
                "profile_id": record.profile.profile_id,
                "display_name": record.profile.display_name,
                "revision": record.profile.revision,
                "default_style": record.profile.default_style,
                "consistency_locked": record.profile.consistency_locked,
                "preferred_engine_id": record.profile.preferred_engine_id,
                "supported_languages": list(record.profile.supported_languages),
                "source_kind": record.profile.source.kind.value,
                "bound_engines": sorted(binding.engine_id for binding in record.bindings if binding.enabled),
            }
        )
    return rows


def _voices_get(context: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    profile_id = str(params.get("profile_id") or "").strip()
    if not profile_id:
        raise RpcError(-32602, "Missing required param: profile_id", kind=SpeechErrorKind.INVALID_ARGUMENT)
    record = context.voice_profiles.get(profile_id)
    if record is None:
        raise RpcError(-32004, f"Voice profile '{profile_id}' was not found.", kind=SpeechErrorKind.NOT_FOUND)
    payload = _clean(record)
    # The persisted schema v2 removed duplicate top-level fields, but PR #9 already
    # exposed them through voices.get. Preserve that RPC response shape for old local
    # clients while making `profile.*` the canonical source for new clients.
    if isinstance(payload, dict):
        payload["bindings"] = [_clean(binding) for binding in record.bindings]
        payload["pronunciation_hints"] = dict(record.pronunciation_hints)
        payload["preferred_styles"] = list(record.preferred_styles)
    return payload


def _parse_synthesis_request(params: dict[str, Any]) -> SpeechSynthesisRequest:
    version = params.get("schema_version", SCHEMA_VERSION)
    try:
        version = int(version)
    except (TypeError, ValueError) as exc:
        raise RpcError(-32602, "schema_version must be an integer.", kind=SpeechErrorKind.INVALID_ARGUMENT) from exc
    if version != SCHEMA_VERSION:
        raise RpcError(
            -32020,
            "Speech synthesis request schema is incompatible with this server.",
            kind=SpeechErrorKind.PROTOCOL_INCOMPATIBLE,
            data={"request_schema_version": version, "server_schema_version": SCHEMA_VERSION},
        )
    try:
        priority = Priority(str(params.get("priority", Priority.AUTO.value)))
    except ValueError as exc:
        raise RpcError(-32602, "Unknown synthesis priority.", kind=SpeechErrorKind.INVALID_ARGUMENT) from exc

    raw_events = params.get("events") or []
    if not isinstance(raw_events, list):
        raise RpcError(-32602, "events must be a list.", kind=SpeechErrorKind.INVALID_ARGUMENT)
    events: list[SpeechEvent] = []
    for item in raw_events:
        if not isinstance(item, dict) or not isinstance(item.get("kind"), str):
            raise RpcError(-32602, "Each speech event must be an object with a kind.", kind=SpeechErrorKind.INVALID_ARGUMENT)
        try:
            events.append(SpeechEvent(**item))
        except TypeError as exc:
            raise RpcError(-32602, "Speech event has unknown or invalid fields.", kind=SpeechErrorKind.INVALID_ARGUMENT) from exc

    hints = params.get("pronunciation_hints") or {}
    if not isinstance(hints, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in hints.items()):
        raise RpcError(-32602, "pronunciation_hints must map strings to strings.", kind=SpeechErrorKind.INVALID_ARGUMENT)
    metadata = params.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise RpcError(-32602, "metadata must be a JSON object.", kind=SpeechErrorKind.INVALID_ARGUMENT)

    return SpeechSynthesisRequest(
        text=str(params.get("text") or ""),
        voice_profile_id=str(params.get("voice_profile_id") or ""),
        language=str(params.get("language") or "auto"),
        style=str(params.get("style") or "auto"),
        priority=priority,
        voice_revision=int(params["voice_revision"]) if params.get("voice_revision") is not None else None,
        engine_override=str(params["engine_override"]) if params.get("engine_override") else None,
        events=tuple(events),
        pronunciation_hints=dict(hints),
        metadata=dict(metadata),
        schema_version=version,
    )


def _synthesis_error_code(kind: SpeechErrorKind) -> int:
    return {
        SpeechErrorKind.INVALID_ARGUMENT: -32602,
        SpeechErrorKind.NOT_FOUND: -32004,
        SpeechErrorKind.NO_COMPATIBLE_ENGINE: -32010,
        SpeechErrorKind.ENGINE_UNAVAILABLE: -32011,
        SpeechErrorKind.PROTOCOL_INCOMPATIBLE: -32020,
        SpeechErrorKind.MODEL_NOT_INSTALLED: -32021,
        SpeechErrorKind.VOICE_REFERENCE_MISSING: -32022,
        SpeechErrorKind.UNSUPPORTED_VOICE_SOURCE: -32023,
        SpeechErrorKind.GENERATION_FAILED: -32030,
        SpeechErrorKind.CANCELLED: -32031,
    }.get(kind, -32603)


def _synthesize(context: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    request = _parse_synthesis_request(params)
    try:
        return context.synthesis.synthesize(request).to_dict()
    except SynthesisError as exc:
        raise RpcError(_synthesis_error_code(exc.kind), str(exc), kind=exc.kind, data=exc.data) from exc


def _artifact_from_params(params: dict[str, Any]) -> ArtifactRef:
    raw = params.get("artifact")
    if not isinstance(raw, dict):
        raise RpcError(-32602, "artifact must be an ArtifactRef object.", kind=SpeechErrorKind.INVALID_ARGUMENT)
    try:
        return ArtifactRef(**raw)
    except TypeError as exc:
        raise RpcError(-32602, "artifact reference is invalid.", kind=SpeechErrorKind.INVALID_ARGUMENT) from exc


def _artifact_materialize(context: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    artifact = _artifact_from_params(params)
    destination_value = params.get("destination")
    if not isinstance(destination_value, str) or not destination_value.strip():
        raise RpcError(-32602, "destination path is required.", kind=SpeechErrorKind.INVALID_ARGUMENT)
    destination = Path(destination_value).expanduser().resolve()
    overwrite = bool(params.get("overwrite", False))
    if destination.exists() and not overwrite:
        raise RpcError(-32602, "Destination already exists; set overwrite=true explicitly.", kind=SpeechErrorKind.INVALID_ARGUMENT)
    try:
        source = context.artifacts.resolve(artifact)
    except FileNotFoundError as exc:
        raise RpcError(-32004, "Artifact is missing.", kind=SpeechErrorKind.NOT_FOUND) from exc
    except ValueError as exc:
        raise RpcError(-32022, "Artifact failed validation.", kind=SpeechErrorKind.VOICE_REFERENCE_MISSING) from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {"artifact_id": artifact.artifact_id, "materialized": True, "size_bytes": destination.stat().st_size}


Handler = Callable[[RpcContext, dict[str, Any]], Any]


METHODS: dict[str, Handler] = {
    "health": _health,
    "protocol.info": _protocol_info,
    "capabilities.list": _capabilities,
    "engines.list": _engines,
    "route.decide": _route,
    "voices.list": _voices_list,
    "voices.get": _voices_get,
    "speech.synthesize": _synthesize,
    "artifacts.materialize": _artifact_materialize,
}


class SpeechRpcServer:
    def __init__(self, context: RpcContext):
        self.context = context

    @staticmethod
    def _response(request_id: Any, *, result: Any = None, error: RpcError | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "id": request_id}
        if error is None:
            payload["result"] = _clean(result)
        else:
            error_data: dict[str, Any] = {"kind": error.kind.value}
            if isinstance(error.data, dict):
                error_data.update(_clean(error.data))
            elif error.data is not None:
                error_data["details"] = _clean(error.data)
            payload["error"] = {"code": error.code, "message": error.message, "data": error_data}
        return payload

    def handle(self, request: Any) -> dict[str, Any] | None:
        if not isinstance(request, dict):
            return self._response(None, error=RpcError(-32600, "Invalid Request", kind=SpeechErrorKind.INVALID_ARGUMENT))
        request_id = request.get("id")
        is_notification = "id" not in request
        if request.get("jsonrpc") != JSONRPC_VERSION or not isinstance(request.get("method"), str):
            return None if is_notification else self._response(
                request_id, error=RpcError(-32600, "Invalid Request", kind=SpeechErrorKind.INVALID_ARGUMENT)
            )
        method = request["method"]
        handler = METHODS.get(method)
        if handler is None:
            return None if is_notification else self._response(
                request_id, error=RpcError(-32601, "Method not found", kind=SpeechErrorKind.INVALID_ARGUMENT)
            )
        try:
            params = _require_dict(request.get("params"))
            result = handler(self.context, params)
        except RpcError as exc:
            return None if is_notification else self._response(request_id, error=exc)
        except Exception as exc:  # boundary: never expose a Python traceback to clients
            error = RpcError(
                -32603,
                "Internal error",
                kind=SpeechErrorKind.INTERNAL,
                data={"error_class": type(exc).__name__},
            )
            return None if is_notification else self._response(request_id, error=error)
        return None if is_notification else self._response(request_id, result=result)

    def serve(self, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
        for raw in stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                response = self._response(
                    None, error=RpcError(-32700, "Parse error", kind=SpeechErrorKind.INVALID_ARGUMENT)
                )
            else:
                response = self.handle(request)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
                stdout.flush()
        return 0


def run_stdio_server(data_dir: str | Path) -> int:
    context = RpcContext(Path(data_dir).expanduser())
    context.data_dir.mkdir(parents=True, exist_ok=True)
    return SpeechRpcServer(context).serve()
