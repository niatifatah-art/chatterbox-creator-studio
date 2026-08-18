from __future__ import annotations

from studio.engine_registry import EngineManifest, engines_for, manifest_for
from studio.protocol import (
    ArtifactRef,
    Capability,
    EngineStatus,
    Priority,
    Provenance,
    SpeechArtifact,
    SpeechSynthesisRequest,
    VoiceProfile,
    VoiceSource,
    VoiceSourceKind,
)
from studio.settings import DEFAULT_SETTINGS
from studio.speech_router import RouteRequest, route
from studio.telemetry import Telemetry, TelemetryConfig


def test_protocol_serializes_without_absolute_machine_paths():
    request = SpeechSynthesisRequest(
        text="Hello",
        voice_profile_id="voice_demo",
        language="en",
        priority=Priority.CONSISTENCY,
    )
    payload = request.to_dict()
    assert payload["priority"] == "consistency_first"
    assert payload["schema_version"] == 1

    artifact = SpeechArtifact(
        audio=ArtifactRef("art_1", "audio/wav", "local://artifacts/art_1"),
        duration_seconds=1.25,
        language="en",
        voice_profile_id="voice_demo",
        voice_revision=2,
        style="creator",
        provenance=Provenance(engine_id="chatterbox-v3", model_revision="abc123"),
    )
    serialized = artifact.to_dict()
    assert serialized["audio"]["uri"].startswith("local://")
    assert "C:\\" not in str(serialized)
    assert "/home/" not in str(serialized)


def test_voice_profile_is_engine_independent_and_versioned():
    profile = VoiceProfile(
        profile_id="vp_demo",
        display_name="Narrator",
        source=VoiceSource(kind=VoiceSourceKind.CLONE, voice_id="reference_1"),
        revision=3,
        consistency_locked=True,
        supported_languages=("en", "ar"),
    )
    payload = profile.to_dict()
    assert payload["source"]["kind"] == "clone"
    assert payload["revision"] == 3
    assert "engine_id" not in payload["source"]


def test_registry_exposes_capabilities_runtime_and_model_assets():
    qwen = manifest_for("qwen3-tts")
    assert qwen.supports(Capability.SYNTHESIZE, Capability.VOICE_DESIGN)
    assert qwen.status == EngineStatus.CATALOGUED
    chatterbox = manifest_for("chatterbox-v3")
    assert chatterbox.supports(Capability.VOICE_CLONE)
    assert chatterbox.runtime_id == "chatterbox"
    assert chatterbox.model_ids == ("multilingual-v3",)
    assert any(item.engine_id == "faster-whisper" for item in engines_for(Capability.TRANSCRIBE))


def test_router_accepts_a_new_manifest_without_engine_name_branch(monkeypatch):
    future = EngineManifest(
        engine_id="future-local-tts",
        display_name="Future Local TTS",
        family="future",
        capabilities=frozenset({Capability.SYNTHESIZE, Capability.VOICE_CLONE}),
        languages=("en",),
        resource_tier="medium",
        code_license="MIT",
        weights_license="MIT",
        runtime_id="future-runtime",
        model_ids=("future-model-v1",),
        status=EngineStatus.SUPPORTED,
    )
    monkeypatch.setattr("studio.speech_router.ENGINE_MANIFESTS", {future.engine_id: future})
    decision = route(
        RouteRequest(
            capability=Capability.SYNTHESIZE,
            language="en",
            needs_voice_clone=True,
            installed_engines=frozenset({future.engine_id}),
        )
    )
    assert decision.engine_id == future.engine_id
    assert not decision.requires_install


def test_arabic_clone_prefers_current_supported_multilingual_engine():
    decision = route(
        RouteRequest(
            capability=Capability.SYNTHESIZE,
            language="ar",
            needs_voice_clone=True,
            installed_engines=frozenset({"chatterbox-v3"}),
        )
    )
    assert decision.engine_id == "chatterbox-v3"
    assert not decision.requires_install


def test_consistency_lock_beats_new_catalogued_quality_candidate():
    decision = route(
        RouteRequest(
            capability=Capability.SYNTHESIZE,
            language="en",
            needs_voice_clone=True,
            priority=Priority.CONSISTENCY,
            consistency_engine="chatterbox-v3",
            installed_engines=frozenset({"chatterbox-v3", "qwen3-tts"}),
        )
    )
    assert decision.engine_id == "chatterbox-v3"
    assert "pinned voice identity" in decision.reason


def test_best_quality_does_not_promote_uncertified_engine_over_supported_route():
    decision = route(
        RouteRequest(
            capability=Capability.SYNTHESIZE,
            language="en",
            needs_voice_clone=True,
            priority=Priority.BEST,
            installed_engines=frozenset({"chatterbox-v3", "qwen3-tts"}),
        )
    )
    assert decision.engine_id == "chatterbox-v3"
    assert manifest_for(decision.engine_id).status == EngineStatus.SUPPORTED


def test_voice_design_can_select_catalogued_engine_but_marks_install_needed():
    decision = route(
        RouteRequest(
            capability=Capability.SYNTHESIZE,
            language="en",
            needs_voice_design=True,
            priority=Priority.BEST,
        )
    )
    assert decision.engine_id == "qwen3-tts"
    assert decision.requires_install


def test_manual_override_rejects_unsupported_language():
    try:
        route(
            RouteRequest(
                capability=Capability.SYNTHESIZE,
                language="ar",
                engine_override="chatterbox-nano",
            )
        )
    except ValueError as exc:
        assert "does not support language" in str(exc)
    else:
        raise AssertionError("English-only manual override should not accept Arabic")


def test_telemetry_is_off_by_default():
    assert DEFAULT_SETTINGS["telemetry_enabled"] is False
    telemetry = Telemetry(TelemetryConfig(enabled=False, project_token="ph_test"))
    assert telemetry.capture("studio_opened", {"engine_id": "chatterbox-v3"}) is False


def test_telemetry_drops_user_content_properties():
    safe = Telemetry._safe_properties(
        {
            "engine_id": "chatterbox-v3",
            "priority": "auto",
            "script": "private text",
            "transcript": "private transcript",
            "voice_path": "C:/private/voice.wav",
            "account_name": "private account",
        }
    )
    assert safe["engine_id"] == "chatterbox-v3"
    assert "script" not in safe
    assert "transcript" not in safe
    assert "voice_path" not in safe
    assert "account_name" not in safe
