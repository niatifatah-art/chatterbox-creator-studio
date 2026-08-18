from __future__ import annotations

import json
from pathlib import Path

import pytest

from studio.model_manager import LocalModelManager


def test_select_snapshot_records_exact_path_and_revision(tmp_path: Path):
    state = tmp_path / "model_state.json"
    snapshot = tmp_path / "cache" / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    manager = LocalModelManager(state)

    status = manager.select_snapshot("nano", snapshot)

    assert status.installed is True
    assert Path(status.snapshot_path or "").resolve() == snapshot.resolve()
    assert status.revision == "abc123"
    payload = json.loads(state.read_text(encoding="utf-8"))
    assert Path(payload["models"]["nano"]["snapshot_path"]).resolve() == snapshot.resolve()
    assert payload["models"]["nano"]["revision"] == "abc123"
    assert payload["models"]["nano"]["selected_at"]


def test_select_snapshot_can_preserve_explicit_revision_label(tmp_path: Path):
    snapshot = tmp_path / "snapshot-folder"
    snapshot.mkdir()
    manager = LocalModelManager(tmp_path / "state.json")

    status = manager.select_snapshot("multilingual-v3", snapshot, revision="resolved-commit-sha")

    assert status.revision == "resolved-commit-sha"
    assert Path(status.snapshot_path or "").resolve() == snapshot.resolve()


def test_select_snapshot_rejects_unknown_model_and_missing_path(tmp_path: Path):
    manager = LocalModelManager(tmp_path / "state.json")
    existing = tmp_path / "existing"
    existing.mkdir()

    with pytest.raises(ValueError, match="Unknown model"):
        manager.select_snapshot("unknown", existing)
    with pytest.raises(FileNotFoundError, match="snapshot"):
        manager.select_snapshot("nano", tmp_path / "missing")
