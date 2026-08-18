from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from studio.protocol import (
    RPC_PROTOCOL_VERSION,
    SPEECH_SCHEMA_VERSION,
    SpeechErrorKind,
    VoiceSourceKind,
)
from studio.rpc import RpcContext, SpeechRpcServer
from studio.rpc_client import SpeechRpcClient, SpeechRpcClientError
from studio.voice_profile_store import VoiceProfileStore


def test_rpc_health_protocol_and_engine_discovery_are_model_free(tmp_path):
    server = SpeechRpcServer(RpcContext(tmp_path))
    health = server.handle({"jsonrpc": "2.0", "id": 1, "method": "health", "params": {}})
    assert health is not None
    assert health["result"]["status"] == "ok"
    assert health["result"]["rpc_protocol_version"] == RPC_PROTOCOL_VERSION
    assert health["result"]["speech_schema_version"] == SPEECH_SCHEMA_VERSION
    assert health["result"]["min_rpc_protocol_version"] <= RPC_PROTOCOL_VERSION
    assert health["result"]["min_speech_schema_version"] <= SPEECH_SCHEMA_VERSION

    info = server.handle({"jsonrpc": "2.0", "id": 2, "method": "protocol.info", "params": {}})
    assert info is not None
    assert info["result"]["rpc_protocol_version"] == RPC_PROTOCOL_VERSION
    assert info["result"]["transport"] == "stdio-jsonl"

    engines = server.handle({"jsonrpc": "2.0", "id": 3, "method": "engines.list", "params": {}})
    assert engines is not None
    rows = {row["engine_id"]: row for row in engines["result"]}
    assert "chatterbox-v3" in rows
    assert "qwen3-tts" in rows
    assert "faster-whisper" in rows
    assert rows["chatterbox-v3"]["runtime_id"] == "chatterbox"
    assert rows["chatterbox-v3"]["model_ids"] == ["multilingual-v3"]


def test_rpc_capability_discovery_distinguishes_supported_from_catalogued(tmp_path):
    server = SpeechRpcServer(RpcContext(tmp_path))
    response = server.handle({"jsonrpc": "2.0", "id": 4, "method": "capabilities.list", "params": {}})
    assert response is not None
    design = next(row for row in response["result"] if row["id"] == "speech.voice.design.v1")
    assert "qwen3-tts" in design["engines"]
    assert design["supported_engines"] == []


def test_rpc_route_keeps_arabic_clone_and_consistency_rules(tmp_path):
    server = SpeechRpcServer(RpcContext(tmp_path))
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "route.decide",
            "params": {
                "capability": "speech.synthesize.v1",
                "language": "ar",
                "needs_voice_clone": True,
                "installed_engines": ["chatterbox-v3"],
            },
        }
    )
    assert response is not None
    assert response["result"]["engine_id"] == "chatterbox-v3"
    assert response["result"]["requires_install"] is False


def test_rpc_lists_voice_profiles_without_account_metadata(tmp_path):
    store = VoiceProfileStore(tmp_path / "voice-profiles")
    store.create("account-narrator", "Narrator", source_kind=VoiceSourceKind.READY, source_voice_id="preset-1")
    server = SpeechRpcServer(RpcContext(tmp_path))
    response = server.handle({"jsonrpc": "2.0", "id": 6, "method": "voices.list", "params": {}})
    assert response is not None
    assert response["result"][0]["profile_id"] == "account-narrator"
    assert "account_name" not in response["result"][0]
    assert "platform" not in response["result"][0]


def test_rpc_returns_stable_error_kinds_without_tracebacks(tmp_path):
    server = SpeechRpcServer(RpcContext(tmp_path))
    missing = server.handle({"jsonrpc": "2.0", "id": 7, "method": "voices.get", "params": {"profile_id": "missing"}})
    assert missing is not None
    assert missing["error"]["code"] == -32004
    assert missing["error"]["data"]["kind"] == SpeechErrorKind.NOT_FOUND.value
    assert "Traceback" not in json.dumps(missing)

    unknown = server.handle({"jsonrpc": "2.0", "id": 8, "method": "nope"})
    assert unknown is not None
    assert unknown["error"]["code"] == -32601
    assert unknown["error"]["data"]["kind"] == SpeechErrorKind.INVALID_ARGUMENT.value


def test_stdio_client_can_spawn_core_negotiate_and_discover(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    client = SpeechRpcClient(
        [sys.executable, "-m", "studio.rpc_main", "--data-dir", str(tmp_path / "rpc-data")],
        cwd=repo_root,
    )
    with client:
        result = client.health()
        assert result["status"] == "ok"
        info = client.ensure_compatible()
        assert info["rpc_protocol_version"] == RPC_PROTOCOL_VERSION
        assert client.capabilities()
        supported = client.engines(include_catalogued=False)
        assert supported
        assert all(row["status"] == "supported" for row in supported)
        route = client.route_decide(
            capability="speech.synthesize.v1",
            language="ar",
            needs_voice_clone=True,
            installed_engines=["chatterbox-v3"],
        )
        assert route["engine_id"] == "chatterbox-v3"


def test_stdio_client_surfaces_semantic_rpc_errors(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    with SpeechRpcClient(
        [sys.executable, "-m", "studio.rpc_main", "--data-dir", str(tmp_path / "rpc-data")],
        cwd=repo_root,
    ) as client:
        with pytest.raises(SpeechRpcClientError) as exc:
            client.voice("missing")
        assert exc.value.code == -32004
        assert exc.value.kind == SpeechErrorKind.NOT_FOUND.value


def test_client_protocol_info_falls_back_to_v1_health_shape():
    client = SpeechRpcClient([sys.executable, "-c", "pass"])

    def fake_call(method: str, _params=None):
        if method == "protocol.info":
            raise SpeechRpcClientError(-32601, "Method not found")
        assert method == "health"
        return {
            "status": "ok",
            "rpc_protocol_version": RPC_PROTOCOL_VERSION,
            "speech_schema_version": SPEECH_SCHEMA_VERSION,
            "transport": "stdio-jsonl",
        }

    client.call = fake_call  # type: ignore[method-assign]
    info = client.ensure_compatible()
    assert info["rpc_protocol_version"] == RPC_PROTOCOL_VERSION
    assert info["speech_schema_version"] == SPEECH_SCHEMA_VERSION


def test_client_fails_early_for_non_overlapping_protocol_range():
    client = SpeechRpcClient([sys.executable, "-c", "pass"])
    client.protocol_info = lambda: {  # type: ignore[method-assign]
        "rpc_protocol_version": 99,
        "min_rpc_protocol_version": 99,
        "speech_schema_version": 99,
        "min_speech_schema_version": 99,
        "transport": "stdio-jsonl",
    }
    with pytest.raises(SpeechRpcClientError) as exc:
        client.ensure_compatible()
    assert exc.value.kind == SpeechErrorKind.PROTOCOL_INCOMPATIBLE.value
