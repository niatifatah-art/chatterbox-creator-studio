from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

# Windowed PyInstaller applications have no stdout/stderr on Windows. Some transitive
# libraries still assume the streams exist, so provide harmless sinks before imports.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import product_app  # noqa: E402


TITLE = "Creator Studio"
HOST = "127.0.0.1"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def _css() -> str:
    parts: list[str] = []
    if product_app.core.CSS_PATH.exists():
        parts.append(product_app.core.CSS_PATH.read_text(encoding="utf-8"))
    if product_app.UI_CSS_PATH.exists():
        parts.append(product_app.UI_CSS_PATH.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _start_server(port: int) -> None:
    product_app.demo.queue(default_concurrency_limit=1).launch(
        server_name=HOST,
        server_port=port,
        share=False,
        inbrowser=False,
        css=_css(),
        show_error=True,
        prevent_thread_lock=True,
    )


def _wait_for_server(url: str, timeout: float = 45.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def _run_backend(port: int) -> threading.Thread:
    thread = threading.Thread(target=_start_server, args=(port,), name="creator-studio-server", daemon=True)
    thread.start()
    return thread


def self_test() -> int:
    """CI/package probe: start the bundled server, verify HTTP, and exit."""
    port = _free_port()
    url = f"http://{HOST}:{port}/"
    _run_backend(port)
    return 0 if _wait_for_server(url, timeout=60.0) else 2


def launch_desktop() -> int:
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
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    return launch_desktop()


if __name__ == "__main__":
    raise SystemExit(main())
