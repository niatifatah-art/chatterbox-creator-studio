from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_minimal_external_client_example_is_model_free(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "examples" / "speech_client_minimal.py"),
            "--data-dir",
            str(tmp_path / "speech-core"),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Speech Core protocol" in result.stdout
    assert "Capabilities:" in result.stdout
    assert "Example route:" in result.stdout
