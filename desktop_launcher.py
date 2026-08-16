from __future__ import annotations

import argparse
import multiprocessing
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

# Windowed PyInstaller applications have no stdout/stderr on Windows. Some transitive
# libraries still assume the streams exist, so provide harmless sinks before imports.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

TITLE = "Creator Studio"
HOST = "127.0.0.1"
_PRODUCT_APP: Any | None = None


def _log(message: str) -> None:
    """Write startup diagnostics only when CI/troubleshooting explicitly requests it."""
    path = os.environ.get("CREATOR_STUDIO_SELF_TEST_LOG")
    if not path:
        return
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")
    except Exception:
        # Diagnostics must never prevent the desktop application from starting.
        pass


def _product_app():
    global _PRODUCT_APP
    if _PRODUCT_APP is None:
        _log("import product_app: start")
        import product_app as loaded

        _PRODUCT_APP = loaded
        _log("import product_app: complete")
    return _PRODUCT_APP


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def _css() -> str:
    product_app = _product_app()
    parts: list[str] = []
    if product_app.core.CSS_PATH.exists():
        parts.append(product_app.core.CSS_PATH.read_text(encoding="utf-8"))
    if product_app.UI_CSS_PATH.exists():
        parts.append(product_app.UI_CSS_PATH.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _start_server(port: int) -> None:
    try:
        product_app = _product_app()
        _log(f"server launch: start on {HOST}:{port}")
        product_app.demo.queue(default_concurrency_limit=1).launch(
            server_name=HOST,
            server_port=port,
            share=False,
            inbrowser=False,
            css=_css(),
            show_error=True,
            prevent_thread_lock=True,
        )
        _log("server launch: returned")
    except BaseException:
        _log("server launch: exception\n" + traceback.format_exc())


def _wait_for_server(url: str, timeout: float = 45.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:
                if response.status == 200:
                    _log("HTTP probe: ready")
                    return True
        except Exception:
            time.sleep(0.25)
    _log("HTTP probe: timed out")
    return False


def _run_backend(port: int) -> threading.Thread:
    thread = threading.Thread(target=_start_server, args=(port,), name="creator-studio-server", daemon=True)
    thread.start()
    _log("server thread: started")
    return thread


def self_test() -> int:
    """CI/package probe: import the product, start the bundled server, verify HTTP, and exit."""
    _log("self-test: entered")
    try:
        _product_app()
        port = _free_port()
        url = f"http://{HOST}:{port}/"
        _run_backend(port)
        code = 0 if _wait_for_server(url, timeout=60.0) else 2
        _log(f"self-test: finished with {code}")
        return code
    except BaseException:
        _log("self-test: exception\n" + traceback.format_exc())
        return 3


def launch_desktop() -> int:
    _product_app()
    port = _free_port()
    url = f"http://{HOST}:{port}/"
    _run_backend(port)
    if not _wait_for_server(url):
        return 2

    try:
        import webview

        webview.create_window(
            TITLE,
            url,
            width=1320,
            height=860,
            min_size=(960, 650),
            resizable=True,
            background_color="#090a0d",
            text_select=True,
        )
        # Windows prefers Edge WebView2 when available. pywebview handles platform
        # selection and keeps the server private on 127.0.0.1.
        webview.start()
        return 0
    except Exception:
        # The packaged application remains usable if WebView2/native embedding is not
        # available on an unusual machine. The local URL is still never exposed as a
        # setup step; it opens automatically in the default browser instead.
        webbrowser.open(url, new=1)
        while True:
            time.sleep(60)


def main() -> int:
    # Frozen Windows executables should initialize multiprocessing before importing
    # heavyweight libraries that can use it internally.
    multiprocessing.freeze_support()
    _log("launcher: main entered")

    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_test:
        # Gradio/uvicorn can leave helper threads alive after an HTTP probe. Force exit
        # only in the hidden CI mode after recording the exact startup stage.
        code = self_test()
        _log(f"launcher: forcing self-test exit {code}")
        os._exit(code)
    return launch_desktop()


if __name__ == "__main__":
    raise SystemExit(main())
