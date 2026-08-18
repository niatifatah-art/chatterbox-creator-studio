from __future__ import annotations

from dataclasses import dataclass

from studio.engine_registry import ENGINE_MANIFESTS, EngineManifest
from studio.protocol import Capability, Priority


@dataclass(frozen=True, slots=True)
class RouteRequest:
    capability: Capability
    language: str = "en"
    priority: Priority = Priority.AUTO
    needs_voice_clone: bool = False
    needs_voice_design: bool = False
    needs_ready_voice: bool = False
    weak_cpu: bool = False
    installed_engines: frozenset[str] = frozenset()
    consistency_engine: str | None = None
    engine_override: str | None = None


@dataclass(frozen=True, slots=True)
class RouteDecision:
    engine_id: str
    reason: str
    requires_install: bool


def _supports_language(manifest: EngineManifest, language: str) -> bool:
    normalized = (language or "en").lower().split("-", 1)[0]
    return "*" in manifest.languages or normalized in manifest.languages


def _requirements(request: RouteRequest) -> tuple[Capability, ...]:
    required = [request.capability]
    if request.needs_voice_clone:
        required.append(Capability.VOICE_CLONE)
    if request.needs_voice_design:
        required.append(Capability.VOICE_DESIGN)
    if request.needs_ready_voice:
        required.append(Capability.READY_VOICE)
    return tuple(required)


def _candidate_score(manifest: EngineManifest, request: RouteRequest) -> tuple[int, int, str]:
    score = 0
    installed = manifest.engine_id in request.installed_engines
    if installed:
        score += 30

    if request.consistency_engine and manifest.engine_id == request.consistency_engine:
        score += 100

    if request.priority == Priority.CONSISTENCY and request.consistency_engine == manifest.engine_id:
        score += 100
    elif request.priority == Priority.LIGHTWEIGHT or request.weak_cpu:
        score += {"ultra_light": 40, "light": 30, "medium": 10, "heavy": 0}.get(manifest.resource_tier, 0)
    elif request.priority == Priority.FAST:
        score += {"ultra_light": 30, "light": 25, "medium": 20, "heavy": 5}.get(manifest.resource_tier, 0)
    elif request.priority == Priority.BEST:
        if manifest.engine_id in {"qwen3-tts", "chatterbox-v3"}:
            score += 30
    else:
        if manifest.engine_id == "chatterbox-v3" and request.language.lower().startswith("ar"):
            score += 35
        if manifest.engine_id == "faster-whisper" and request.capability == Capability.TRANSCRIBE:
            score += 25
        if manifest.engine_id == "kokoro" and request.needs_ready_voice and request.weak_cpu:
            score += 25

    return score, 1 if installed else 0, manifest.engine_id


def route(request: RouteRequest) -> RouteDecision:
    requirements = _requirements(request)

    if request.engine_override:
        try:
            manifest = ENGINE_MANIFESTS[request.engine_override]
        except KeyError as exc:
            raise ValueError(f"Unknown engine override: {request.engine_override}") from exc
        if not manifest.supports(*requirements):
            raise ValueError(f"{manifest.display_name} does not support the requested speech capability.")
        if not _supports_language(manifest, request.language):
            raise ValueError(f"{manifest.display_name} does not support language '{request.language}'.")
        return RouteDecision(
            engine_id=manifest.engine_id,
            reason="Manual engine override.",
            requires_install=manifest.engine_id not in request.installed_engines,
        )

    candidates = [
        manifest
        for manifest in ENGINE_MANIFESTS.values()
        if manifest.supports(*requirements) and _supports_language(manifest, request.language)
    ]
    if not candidates:
        raise ValueError("No compatible speech engine is registered for this request.")

    # Catalogued engines are visible for discovery/manual install, but they cannot
    # displace a certified route merely because a heuristic gives them a higher
    # score. We fall back to catalogued candidates only when no certified engine
    # can satisfy the requested capability at all (for example Voice Design before
    # its first engine is certified).
    supported = [manifest for manifest in candidates if manifest.status == "supported"]
    routable = supported or candidates

    # A consistency-pinned engine is allowed only when it remains compatible. If
    # it is catalogued rather than certified, keep it only when the caller asked
    # explicitly for consistency and no certified route can preserve that identity.
    if request.consistency_engine:
        pinned = next((manifest for manifest in candidates if manifest.engine_id == request.consistency_engine), None)
        if pinned is not None and pinned.status == "supported":
            routable = [pinned, *[manifest for manifest in routable if manifest.engine_id != pinned.engine_id]]

    routable.sort(key=lambda manifest: _candidate_score(manifest, request), reverse=True)
    winner = routable[0]
    reason_parts = []
    if request.consistency_engine == winner.engine_id:
        reason_parts.append("keeps the pinned voice identity")
    if request.language:
        reason_parts.append(f"supports {request.language}")
    if request.priority == Priority.LIGHTWEIGHT or request.weak_cpu:
        reason_parts.append("fits the low-resource preference")
    if winner.status != "supported":
        reason_parts.append("is the only compatible catalogued route and still needs local certification")
    if not reason_parts:
        reason_parts.append("best compatible certified route")

    return RouteDecision(
        engine_id=winner.engine_id,
        reason="; ".join(reason_parts).capitalize() + ".",
        requires_install=winner.engine_id not in request.installed_engines,
    )
