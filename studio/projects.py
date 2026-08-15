from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str, fallback: str = "project") -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or fallback).strip()).strip("-._")
    return clean or fallback


@dataclass(frozen=True)
class ProjectSummary:
    id: str
    name: str
    updated_at: str


class ProjectStore:
    """Small, local, JSON-backed creator project store.

    A project is intentionally portable: one JSON document plus copied take WAV/JSON
    pairs. Nothing here depends on Gradio or Chatterbox internals.
    """

    SCHEMA_VERSION = 1

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _project_dir(self, project_id: str) -> Path:
        safe = _slug(project_id)
        return self.directory / safe

    def _project_file(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "project.json"

    def list(self) -> list[ProjectSummary]:
        items: list[ProjectSummary] = []
        for path in self.directory.glob("*/project.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            items.append(
                ProjectSummary(
                    id=str(payload.get("id") or path.parent.name),
                    name=str(payload.get("name") or path.parent.name),
                    updated_at=str(payload.get("updated_at") or ""),
                )
            )
        return sorted(items, key=lambda item: item.updated_at, reverse=True)

    def create(self, name: str) -> dict[str, Any]:
        base = _slug(name)
        project_id = base
        index = 2
        while self._project_dir(project_id).exists():
            project_id = f"{base}-{index}"
            index += 1
        now = _utc_now()
        payload: dict[str, Any] = {
            "schema_version": self.SCHEMA_VERSION,
            "id": project_id,
            "name": (name or project_id).strip() or project_id,
            "created_at": now,
            "updated_at": now,
            "script": "",
            "voice": None,
            "model_id": "multilingual-v3",
            "language": "en",
            "settings": {},
            "segments": [],
            "selected_takes": {},
        }
        self.save(project_id, payload)
        return payload

    def load(self, project_id: str | None) -> dict[str, Any] | None:
        if not project_id:
            return None
        path = self._project_file(project_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def save(self, project_id: str, values: dict[str, Any]) -> dict[str, Any]:
        project_id = _slug(project_id)
        path = self._project_file(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        current = self.load(project_id) or {}
        now = _utc_now()
        payload: dict[str, Any] = {
            "schema_version": self.SCHEMA_VERSION,
            "id": project_id,
            "name": str(values.get("name", current.get("name", project_id))),
            "created_at": str(current.get("created_at", values.get("created_at", now))),
            "updated_at": now,
            "script": str(values.get("script", current.get("script", ""))),
            "voice": values.get("voice", current.get("voice")),
            "model_id": str(values.get("model_id", current.get("model_id", "multilingual-v3"))),
            "language": str(values.get("language", current.get("language", "en"))),
            "settings": values.get("settings", current.get("settings", {})) or {},
            "segments": values.get("segments", current.get("segments", [])) or [],
            "selected_takes": values.get("selected_takes", current.get("selected_takes", {})) or {},
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return payload

    def delete(self, project_id: str) -> bool:
        path = self._project_dir(project_id)
        if not path.exists() or path.parent.resolve() != self.directory.resolve():
            return False
        shutil.rmtree(path)
        return True

    def add_take(
        self,
        project_id: str,
        audio_path: str | Path,
        metadata_path: str | Path | None = None,
        segment_id: str = "full",
    ) -> dict[str, str]:
        project = self.load(project_id)
        if project is None:
            raise FileNotFoundError("Project not found.")
        audio = Path(audio_path)
        if not audio.exists():
            raise FileNotFoundError("Generated audio no longer exists.")
        segment = _slug(segment_id, "full")
        target_dir = self._project_dir(project_id) / "takes" / segment
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        target_audio = target_dir / f"{stamp}.wav"
        shutil.copy2(audio, target_audio)
        result = {"audio": str(target_audio)}
        if metadata_path:
            metadata = Path(metadata_path)
            if metadata.exists():
                target_meta = target_audio.with_suffix(".json")
                shutil.copy2(metadata, target_meta)
                result["metadata"] = str(target_meta)
        return result

    def list_takes(self, project_id: str, segment_id: str = "full") -> list[Path]:
        segment = _slug(segment_id, "full")
        take_dir = self._project_dir(project_id) / "takes" / segment
        if not take_dir.exists():
            return []
        return sorted(take_dir.glob("*.wav"), key=lambda path: path.stat().st_mtime, reverse=True)

    def select_take(self, project_id: str, segment_id: str, take_path: str | Path) -> dict[str, Any]:
        project = self.load(project_id)
        if project is None:
            raise FileNotFoundError("Project not found.")
        take = Path(take_path)
        project_root = self._project_dir(project_id).resolve()
        try:
            relative = take.resolve().relative_to(project_root)
        except (OSError, ValueError) as exc:
            raise ValueError("Selected take must belong to this project.") from exc
        selected = dict(project.get("selected_takes") or {})
        selected[_slug(segment_id, "full")] = str(relative)
        project["selected_takes"] = selected
        return self.save(project_id, project)
