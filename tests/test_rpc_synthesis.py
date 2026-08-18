from __future__ import annotations

import sys
from pathlib import Path

import pytest

from studio.artifact_store import ArtifactStore
from studio.protocol import SpeechErrorKind, SpeechSynthesisRequest, VoiceSourceKind
from studio.rpc import RpcContext, SpeechRpcServer
from studio.rpc_client import SpeechRpcClient, SpeechRpcClientError
from studio.voice_profile_store import VoiceProfileStore


def test_rpc_synthesis_missing_model_is_structured_and_never_turns_into_download(tmp_path: Path):
    data = tmp_path / "speech-core"
    profiles = VoiceProfileStore(data / "voice-profiles")
    artifacts = ArtifactStore(data / "artifacts")
    reference_file = tmp_path / "reference.wav"
    reference_file.write_bytes(b"not-read-before-model-check")
    reference = artifacts.register_file(reference_file, artifact_id="ref", mime_type="audio/wav")
    profiles.create("voice", "Voice", source_kind=VoiceSourceKind.CLONE, reference=reference)

    server = SpeechRpcServer(RpcContext(data))
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "speech.synthesize",
            "params": SpeechSynthesisRequest(
                text="Hello",
                voice_profile_id="voice",
                language="en",
                engine_override="chatterbox-v3",
            ).to_dict(),
        }
    )
    assert response is not None
    assert response["error"]["code"] == -32021
    assert response["error"]["data"]["kind"] == SpeechErrorKind.MODEL_NOT_INSTALLED.value
    assert response["error"]["data"]["model_id"] == "multilingual-v3"


def test_public_client_surfaces_synthesis_semantic_error_from_real_sidecar(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    data = tmp_path / "speech-core"
    profiles = VoiceProfileStore(data / "voice-profiles")
    artifacts = ArtifactStore(data / "artifacts")
    source = tmp_path / "ref.wav"
    source.write_bytes(b"dummy")
    ref = artifacts.register_file(source, artifact_id="ref", mime_type="audio/wav")
    profiles.create("voice", "Voice", source_kind=VoiceSourceKind.CLONE, reference=ref)

    with SpeechRpcClient(
        [sys.executable, "-m", "studio.rpc_main", "--data-dir", str(data)],
        cwd=repo_root,
    ) as client:
        with pytest.raises(SpeechRpcClientError) as exc:
            client.synthesize(
                SpeechSynthesisRequest(
                    text="Hello",
                    voice_profile_id="voice",
                    engine_override="chatterbox-v3",
                )
            )
        assert exc.value.kind == SpeechErrorKind.MODEL_NOT_INSTALLED.value
        assert isinstance(exc.value.data, dict)
        assert exc.value.data["model_id"] == "multilingual-v3"


def test_artifact_materialization_copies_to_caller_path_without_exposing_internal_path(tmp_path: Path):
    data = tmp_path / "speech-core"
    context = RpcContext(data)
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    ref = context.artifacts.register_file(source, artifact_id="generated", mime_type="audio/wav")
    destination = tmp_path / "caller" / "narration.wav"

    server = SpeechRpcServer(context)
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "artifacts.materialize",
            "params": {"artifact": ref.to_dict(), "destination": str(destination)},
        }
    )
    assert response is not None and "result" in response
    assert response["result"] == {"artifact_id": "generated", "materialized": True, "size_bytes": 5}
    assert destination.read_bytes() == b"audio"
    assert str(context.artifacts.directory) not in str(response)

    second = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "artifacts.materialize",
            "params": {"artifact": ref.to_dict(), "destination": str(destination)},
        }
    )
    assert second is not None
    assert second["error"]["data"]["kind"] == SpeechErrorKind.INVALID_ARGUMENT.value
