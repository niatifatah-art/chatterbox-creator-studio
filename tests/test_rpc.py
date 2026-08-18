from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from studio.protocol import VoiceSourceKind
from studio.rpc import RpcContext, SpeechRpcServer
from studio.rpc_client import SpeechRpcClient, SpeechRpcClientError
from studio.voice_profile_store import VoiceProfileStore


def test_rpc_health_and_engine_discovery_are_model_free(tmp_path):
    server = SpeechRpcServer(RpcContext(tmp_path))
    health = server.handle({"jsonrpc": "2.0", "id": 1, "method": "health", "params": {}})
    assert health == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "status": "ok",
            "rpc_protocol_version": 1,
            "speech_schema_version": 1,
            "transport": "stdio-jsonl",
        },
    }

    engines = server.handle({"jsonrpc": "2.0", "id": 2, "method": "engines.list", "params": {}})
    assert engines is not None
    ids = {row["engine_id"] for row in engines["result"]}
    assert "chatterbox-v3" in ids
    assert "qwen3-tts" in ids
    assert "faster-whisper" in ids


def test_rpc_route_keeps_arabic_clone_and_consistency_rules(tmp_path):
    server = SpeechRpcServer(RpcContext(tmp_path))
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
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
    response = server.handle({"jsonrpc": "2.0", "id": 4, "method": "voices.list", "params": {}})
    assert response is not None
    assert response["result"][0]["profile_id"] == "account-narrator"
    assert "account_name" not in response["result"][0]
    assert "platform" not in response["result"][0]


def test_rpc_returns_jsonrpc_errors_without_tracebacks(tmp_path):
    server = SpeechRpcServer(RpcContext(tmp_path))
    missing = server.handle({"jsonrpc": "2.0", "id": 5, "method": "voices.get", "params": {"profile_id": "missing"}})
    assert missing is not None
    assert missing["error"]["code"] == -32004
    assert "Traceback" not in json.dumps(missing)

    unknown = server.handle({"jsonrpc": "2.0", "id": 6, "method": "nope"})
    assert unknown is not None
    assert unknown["error"]["code"] == -32601


def test_stdio_client_can_spawn_core_and_call_health(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    client = SpeechRpcClient(
        [sys.executable, "-m", "studio.rpc_main", "--data-dir", str(tmp_path / "rpc-data")],
        cwd=repo_root,
    )
    with client:
        result = client.health()
        assert result["status"] == "ok"
        supported = client.engines(include_catalogued=False)
        assert supported
        assert all(row["status"] == "supported" for row in supported)


def test_stdio_client_surfaces_rpc_errors(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    with SpeechRpcClient(
        [sys.executable, "-m", "studio.rpc_main", "--data-dir", str(tmp_path / "rpc-data")],
        cwd=repo_root,
    ) as client:
        with pytest.raises(SpeechRpcClientError) as exc:
            client.call("voices.get", {"profile_id": "missing"})
        assert exc.value.code == -32004
