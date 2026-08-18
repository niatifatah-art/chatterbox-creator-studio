from __future__ import annotations

import json
from pathlib import Path

from studio.model_manager import LocalModelManager, LocalModelStatus


def _cache_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "hf" / "hub"
    root.mkdir(parents=True)
    monkeypatch.setenv("HF_HUB_CACHE", str(root))
    return root


def _nano_snapshot(root: Path, revision: str) -> Path:
    cache = root / "models--ResembleAI--chatterbox-nano"
    snapshot = cache / "snapshots" / revision
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    return snapshot


def test_v1_model_state_migrates_additively_and_preserves_selected_revision(tmp_path, monkeypatch):
    root = _cache_root(tmp_path, monkeypatch)
    snapshot = _nano_snapshot(root, "rev1")
    state = tmp_path / "model_state.json"
    state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "models": {
                    "nano": {
                        "snapshot_path": str(snapshot),
                        "revision": "rev1",
                        "selected_at": "old-time",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    manager = LocalModelManager(state)
    status = manager.status("nano")
    payload = json.loads(state.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 2
    assert payload["models"]["nano"]["revision"] == "rev1"
    assert status.installed is True
    assert status.revision == "rev1"
    assert status.source_trusted is True
    assert status.repairable is False


def test_cache_ref_import_selects_exact_snapshot_without_network(tmp_path, monkeypatch):
    root = _cache_root(tmp_path, monkeypatch)
    snapshot = _nano_snapshot(root, "abc123")
    ref = root / "models--ResembleAI--chatterbox-nano" / "refs" / "main"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text("abc123", encoding="utf-8")

    manager = LocalModelManager(tmp_path / "state.json")
    status = manager.status("nano")

    assert Path(status.snapshot_path or "").resolve() == snapshot.resolve()
    assert status.revision == "abc123"
    assert status.source_trusted is True
    persisted = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert persisted["models"]["nano"]["revision"] == "abc123"


def test_v3_missing_expected_files_is_repairable(tmp_path, monkeypatch):
    root = _cache_root(tmp_path, monkeypatch)
    snapshot = root / "models--ResembleAI--chatterbox" / "snapshots" / "bad-v3"
    snapshot.mkdir(parents=True)
    (snapshot / "ve.pt").write_bytes(b"present")

    manager = LocalModelManager(tmp_path / "state.json")
    manager.select_snapshot("multilingual-v3", snapshot, revision="bad-v3")
    status = manager.status("multilingual-v3")
    report = manager.verify("multilingual-v3")

    assert status.installed is True
    assert status.source_trusted is True
    assert status.repairable is True
    assert report.valid is False
    assert "s3gen.pt" in report.missing_files
    assert "Missing expected" in (report.warning or "")


def test_manual_external_snapshot_is_never_deleted_by_remove(tmp_path, monkeypatch):
    _cache_root(tmp_path, monkeypatch)
    external = tmp_path / "user-owned" / "nano"
    external.mkdir(parents=True)
    (external / "config.json").write_text("{}", encoding="utf-8")
    state = tmp_path / "state.json"
    manager = LocalModelManager(state)
    manager.select_snapshot("nano", external, revision="manual")

    status = manager.status("nano")
    assert status.installed is True
    assert status.source_trusted is False
    assert status.repairable is False

    assert manager.remove("nano") is False
    assert external.is_dir()
    assert (external / "config.json").is_file()
    payload = json.loads(state.read_text(encoding="utf-8"))
    assert "nano" not in payload["models"]


def test_stage_promote_and_rollback_keep_working_revision_until_explicit_promotion(tmp_path, monkeypatch):
    root = _cache_root(tmp_path, monkeypatch)
    rev1 = _nano_snapshot(root, "rev1")
    rev2 = _nano_snapshot(root, "rev2")
    manager = LocalModelManager(tmp_path / "state.json")
    manager.select_snapshot("nano", rev1, revision="rev1")

    def fake_check_update(model_id: str, *, offline: bool = False) -> LocalModelStatus:
        current = manager.status(model_id)
        return LocalModelStatus(
            **{**current.__dict__, "update_available": True, "remote_revision": "rev2"}
        )

    def fake_download(model_id: str, *, revision=None, force_files=False, offline=False, progress=None):
        assert model_id == "nano"
        assert revision == "rev2"
        return rev2, "rev2"

    monkeypatch.setattr(manager, "check_update", fake_check_update)
    monkeypatch.setattr(manager, "_download_snapshot", fake_download)

    staged = manager.stage_update("nano")
    assert staged.revision == "rev1"
    assert Path(staged.snapshot_path or "").resolve() == rev1.resolve()
    assert staged.candidate_revision == "rev2"
    assert Path(staged.candidate_snapshot_path or "").resolve() == rev2.resolve()

    promoted = manager.promote_candidate("nano")
    assert promoted.revision == "rev2"
    assert promoted.previous_revision == "rev1"
    assert Path(promoted.previous_snapshot_path or "").resolve() == rev1.resolve()

    rolled = manager.rollback("nano")
    assert rolled.revision == "rev1"
    assert rolled.previous_revision == "rev2"


def test_discard_candidate_never_changes_selected_revision(tmp_path, monkeypatch):
    root = _cache_root(tmp_path, monkeypatch)
    rev1 = _nano_snapshot(root, "rev1")
    rev2 = _nano_snapshot(root, "rev2")
    manager = LocalModelManager(tmp_path / "state.json")
    manager.select_snapshot("nano", rev1, revision="rev1")

    state = manager._load_state()
    state["models"]["nano"]["candidate"] = {
        "snapshot_path": str(rev2),
        "revision": "rev2",
        "staged_at": "now",
    }
    manager._save_state(state)

    assert manager.discard_candidate("nano", delete_cache=False) is True
    status = manager.status("nano")
    assert status.revision == "rev1"
    assert status.candidate_revision is None
    assert rev2.is_dir()
