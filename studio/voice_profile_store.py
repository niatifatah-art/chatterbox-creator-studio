from __future__ import annotations

import json
import re
import shutil
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from studio.protocol import ArtifactRef, EngineBinding, VoiceProfile, VoiceSource, VoiceSourceKind


PROFILE_SCHEMA_VERSION = 2
LEGACY_PROFILE_SCHEMA_VERSION = 1

# Compatibility import for callers/tests created during the v1 foundation. There is
# now one canonical binding contract in studio.protocol.
EngineVoiceBinding = EngineBinding


@dataclass(frozen=True, slots=True)
class StoredVoiceProfile:
    """Persisted voice identity plus store timestamps.

    Bindings, pronunciation hints and preferred styles live inside `VoiceProfile` in
    schema v2. Read-only properties keep the v1 Python call sites compatible while we
    migrate the UI/controller in stages.
    """

    profile: VoiceProfile
    created_at: str = ""
    updated_at: str = ""
    schema_version: int = PROFILE_SCHEMA_VERSION

    @property
    def bindings(self) -> tuple[EngineBinding, ...]:
        return self.profile.engine_bindings

    @property
    def pronunciation_hints(self) -> dict[str, str]:
        return self.profile.pronunciation_hints

    @property
    def preferred_styles(self) -> tuple[str, ...]:
        return self.profile.preferred_styles

    def binding_for(self, engine_id: str) -> EngineBinding | None:
        return next((binding for binding in self.bindings if binding.engine_id == engine_id and binding.enabled), None)


class VoiceProfileStore:
    """Local, versioned voice identity store shared by Speech Core clients.

    Schema v2 has one source of truth: the `VoiceProfile` itself owns engine bindings,
    pronunciation hints and preferred styles. The store owns only timestamps and file
    migration. It deliberately contains no account/platform publishing metadata.
    """

    def __init__(self, directory: str | Path):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.backup_directory = self.directory.parent / "backups" / "voice-profiles-v1"

    @staticmethod
    def _safe_id(value: str) -> str:
        # Python's Unicode regex mode makes \w include Unicode alphanumeric
        # characters. NFKC prevents visually equivalent compatibility forms from
        # becoming different filenames while path separators/punctuation are removed.
        normalized = unicodedata.normalize("NFKC", (value or "voice").strip())
        clean = re.sub(r"[^\w.-]+", "-", normalized, flags=re.UNICODE).strip("-._").casefold()
        return clean or "voice"

    def path_for(self, profile_id: str) -> Path:
        return self.directory / f"{self._safe_id(profile_id)}.json"

    def list_ids(self) -> tuple[str, ...]:
        return tuple(sorted(path.stem for path in self.directory.glob("*.json") if path.is_file()))

    def list(self) -> tuple[StoredVoiceProfile, ...]:
        rows: list[StoredVoiceProfile] = []
        for profile_id in self.list_ids():
            record = self.get(profile_id)
            if record is not None:
                rows.append(record)
        return tuple(rows)

    @staticmethod
    def _artifact_from_dict(value: dict[str, Any] | None) -> ArtifactRef | None:
        return ArtifactRef(**value) if isinstance(value, dict) else None

    @classmethod
    def _source_from_dict(cls, value: dict[str, Any]) -> VoiceSource:
        row = dict(value or {})
        row["kind"] = VoiceSourceKind(row.get("kind", VoiceSourceKind.SAVED.value))
        if isinstance(row.get("reference"), dict):
            row["reference"] = cls._artifact_from_dict(row["reference"])
        return VoiceSource(**row)

    @classmethod
    def _binding_from_dict(cls, value: dict[str, Any]) -> EngineBinding:
        row = dict(value or {})
        if isinstance(row.get("prompt_artifact"), dict):
            row["prompt_artifact"] = cls._artifact_from_dict(row["prompt_artifact"])
        for key in ("certified_languages", "preferred_languages"):
            if isinstance(row.get(key), list):
                row[key] = tuple(row[key])
        return EngineBinding(**row)

    @classmethod
    def _profile_from_dict(cls, value: dict[str, Any]) -> VoiceProfile:
        row = dict(value or {})
        row["source"] = cls._source_from_dict(row.get("source") or {})
        for key in ("supported_languages", "preferred_styles"):
            if isinstance(row.get(key), list):
                row[key] = tuple(row[key])
        if isinstance(row.get("engine_bindings"), list):
            row["engine_bindings"] = tuple(cls._binding_from_dict(item) for item in row["engine_bindings"])
        row["pronunciation_hints"] = dict(row.get("pronunciation_hints") or {})
        return VoiceProfile(**row)

    @classmethod
    def _decode_v2(cls, payload: dict[str, Any]) -> StoredVoiceProfile:
        return StoredVoiceProfile(
            profile=cls._profile_from_dict(payload["profile"]),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            schema_version=PROFILE_SCHEMA_VERSION,
        )

    @classmethod
    def _decode_v1(cls, payload: dict[str, Any]) -> StoredVoiceProfile:
        """Convert the foundation-era duplicate binding layout into schema v2."""

        old_profile = cls._profile_from_dict(payload["profile"])
        bindings: dict[str, EngineBinding] = {item.engine_id: item for item in old_profile.engine_bindings}
        for item in payload.get("bindings", []):
            if isinstance(item, dict):
                binding = cls._binding_from_dict(item)
                bindings[binding.engine_id] = binding
        profile = replace(
            old_profile,
            engine_bindings=tuple(sorted(bindings.values(), key=lambda item: item.engine_id)),
            pronunciation_hints=dict(payload.get("pronunciation_hints") or old_profile.pronunciation_hints),
            preferred_styles=tuple(payload.get("preferred_styles") or old_profile.preferred_styles or ("natural", "creator")),
        )
        return StoredVoiceProfile(
            profile=profile,
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            schema_version=PROFILE_SCHEMA_VERSION,
        )

    @staticmethod
    def _encode(record: StoredVoiceProfile) -> dict[str, Any]:
        return {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "profile": record.profile.to_dict(),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    def _backup_v1(self, path: Path) -> Path:
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        destination = self.backup_directory / path.name
        if not destination.exists():
            shutil.copy2(path, destination)
        return destination

    @staticmethod
    def _write_payload(path: Path, payload: dict[str, Any]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        # Path.replace() maps to an OS-level replacement on the same filesystem. The
        # temporary file deliberately lives beside the destination.
        tmp.replace(path)

    def _read_payload(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Voice profile '{path.stem}' is unreadable.") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Voice profile '{path.stem}' is invalid.")
        return payload

    def get(self, profile_id: str) -> StoredVoiceProfile | None:
        path = self.path_for(profile_id)
        if not path.exists():
            return None
        payload = self._read_payload(path)
        version = int(payload.get("schema_version", 0))
        if version == PROFILE_SCHEMA_VERSION:
            return self._decode_v2(payload)
        if version == LEGACY_PROFILE_SCHEMA_VERSION:
            record = self._decode_v1(payload)
            self._backup_v1(path)
            self._write_payload(path, self._encode(record))
            return record
        raise ValueError(f"Unsupported voice profile schema version: {version}.")

    def migrate_all(self) -> int:
        """Migrate readable v1 profile files in place, returning the number changed."""

        migrated = 0
        for path in sorted(self.directory.glob("*.json")):
            payload = self._read_payload(path)
            if int(payload.get("schema_version", 0)) == LEGACY_PROFILE_SCHEMA_VERSION:
                self.get(path.stem)
                migrated += 1
        return migrated

    def save(self, record: StoredVoiceProfile) -> StoredVoiceProfile:
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get(record.profile.profile_id)
        created_at = record.created_at or (existing.created_at if existing else now)
        normalized = StoredVoiceProfile(
            profile=record.profile,
            created_at=created_at,
            updated_at=now,
            schema_version=PROFILE_SCHEMA_VERSION,
        )
        path = self.path_for(record.profile.profile_id)
        self._write_payload(path, self._encode(normalized))
        return normalized

    def create(
        self,
        profile_id: str,
        display_name: str,
        *,
        source_kind: VoiceSourceKind,
        source_voice_id: str | None = None,
        reference: ArtifactRef | None = None,
        description: str | None = None,
        supported_languages: tuple[str, ...] = (),
        default_style: str = "creator",
        consistency_locked: bool = True,
        pronunciation_hints: dict[str, str] | None = None,
        preferred_styles: tuple[str, ...] = ("natural", "creator"),
        metadata: dict[str, Any] | None = None,
    ) -> StoredVoiceProfile:
        safe_id = self._safe_id(profile_id)
        if self.path_for(safe_id).exists():
            raise FileExistsError(f"Voice profile '{safe_id}' already exists.")
        profile = VoiceProfile(
            profile_id=safe_id,
            display_name=(display_name or "Voice").strip(),
            source=VoiceSource(
                kind=source_kind,
                voice_id=source_voice_id,
                reference=reference,
                description=description,
            ),
            revision=1,
            default_style=(default_style or "creator").strip().lower(),
            consistency_locked=bool(consistency_locked),
            supported_languages=tuple(supported_languages),
            pronunciation_hints=dict(pronunciation_hints or {}),
            preferred_styles=tuple(preferred_styles),
            metadata=dict(metadata or {}),
        )
        return self.save(StoredVoiceProfile(profile=profile))

    def add_binding(self, profile_id: str, binding: EngineBinding, *, promote_revision: bool = False) -> StoredVoiceProfile:
        record = self.get(profile_id)
        if record is None:
            raise FileNotFoundError(f"Voice profile '{profile_id}' does not exist.")
        bindings = [item for item in record.bindings if item.engine_id != binding.engine_id]
        bindings.append(binding)
        profile = replace(
            record.profile,
            engine_bindings=tuple(sorted(bindings, key=lambda item: item.engine_id)),
            revision=record.profile.revision + (1 if promote_revision else 0),
        )
        return self.save(StoredVoiceProfile(profile=profile, created_at=record.created_at))

    def set_pronunciation_hint(self, profile_id: str, phrase: str, spoken_as: str) -> StoredVoiceProfile:
        record = self.get(profile_id)
        if record is None:
            raise FileNotFoundError(f"Voice profile '{profile_id}' does not exist.")
        hints = dict(record.pronunciation_hints)
        key = (phrase or "").strip()
        value = (spoken_as or "").strip()
        if not key or not value:
            raise ValueError("Pronunciation phrase and spoken form are required.")
        hints[key] = value
        profile = replace(record.profile, pronunciation_hints=hints)
        return self.save(StoredVoiceProfile(profile=profile, created_at=record.created_at))

    def update_display_name(self, profile_id: str, display_name: str) -> StoredVoiceProfile:
        record = self.get(profile_id)
        if record is None:
            raise FileNotFoundError(f"Voice profile '{profile_id}' does not exist.")
        label = (display_name or "Voice").strip() or "Voice"
        return self.save(StoredVoiceProfile(profile=replace(record.profile, display_name=label), created_at=record.created_at))

    def update_metadata(self, profile_id: str, metadata: dict[str, Any]) -> StoredVoiceProfile:
        record = self.get(profile_id)
        if record is None:
            raise FileNotFoundError(f"Voice profile '{profile_id}' does not exist.")
        return self.save(
            StoredVoiceProfile(
                profile=replace(record.profile, metadata=dict(metadata)),
                created_at=record.created_at,
            )
        )

    def delete(self, profile_id: str) -> bool:
        path = self.path_for(profile_id)
        if not path.exists():
            return False
        path.unlink()
        return True
