from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Sequence


class SpeechRpcClientError(RuntimeError):
    def __init__(self, code: int, message: str, data: Any | None = None):
        super().__init__(message)
        self.code = int(code)
        self.data = data


class SpeechRpcClient:
    """Minimal synchronous JSON-RPC client for the local Speech Core sidecar.

    The client deliberately speaks only the public protocol. It does not import
    model adapters, Gradio, ACE, or any engine-specific implementation.
    """

    def __init__(self, command: Sequence[str], *, cwd: str | Path | None = None):
        if not command:
            raise ValueError("Speech Core command is required.")
        self.command = tuple(str(item) for item in command)
        self.cwd = str(Path(cwd).expanduser()) if cwd is not None else None
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._next_id = 1

    @classmethod
    def python_module(cls, data_dir: str | Path, *, python_executable: str | None = None) -> "SpeechRpcClient":
        executable = python_executable or sys.executable
        return cls([executable, "-m", "studio.rpc_main", "--data-dir", str(Path(data_dir).expanduser())])

    @property
    def running(self) -> bool:
        return bool(self._process is not None and self._process.poll() is None)

    def start(self) -> "SpeechRpcClient":
        if self.running:
            return self
        self.close()
        self._process = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        return self

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.poll() is None:
            try:
                if process.stdin:
                    process.stdin.close()
            except OSError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)

    def __enter__(self) -> "SpeechRpcClient":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if not method:
            raise ValueError("RPC method is required.")
        with self._lock:
            self.start()
            process = self._process
            if process is None or process.stdin is None or process.stdout is None:
                raise RuntimeError("Speech Core process is unavailable.")
            request_id = self._next_id
            self._next_id += 1
            request = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
            try:
                process.stdin.write(json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n")
                process.stdin.flush()
                raw = process.stdout.readline()
            except (BrokenPipeError, OSError) as exc:
                raise RuntimeError("Speech Core transport closed unexpectedly.") from exc
            if not raw:
                stderr = ""
                if process.stderr is not None and process.poll() is not None:
                    stderr = process.stderr.read()[-1000:]
                suffix = f" ({stderr.strip()})" if stderr.strip() else ""
                raise RuntimeError(f"Speech Core returned no response{suffix}.")
            try:
                response = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Speech Core returned invalid JSON.") from exc
            if response.get("id") != request_id:
                raise RuntimeError("Speech Core response ID did not match the request.")
            error = response.get("error")
            if isinstance(error, dict):
                raise SpeechRpcClientError(
                    int(error.get("code", -32603)),
                    str(error.get("message") or "Speech Core RPC error"),
                    error.get("data"),
                )
            if "result" not in response:
                raise RuntimeError("Speech Core response is missing a result.")
            return response["result"]

    def health(self) -> dict[str, Any]:
        result = self.call("health")
        if not isinstance(result, dict):
            raise RuntimeError("Speech Core health response is invalid.")
        return result

    def engines(self, *, include_catalogued: bool = True) -> list[dict[str, Any]]:
        result = self.call("engines.list", {"include_catalogued": include_catalogued})
        if not isinstance(result, list):
            raise RuntimeError("Speech Core engine response is invalid.")
        return result

    def voices(self) -> list[dict[str, Any]]:
        result = self.call("voices.list")
        if not isinstance(result, list):
            raise RuntimeError("Speech Core voice response is invalid.")
        return result
