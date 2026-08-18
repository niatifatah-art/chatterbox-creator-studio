from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from studio.protocol import ArtifactRef, VoiceProfile, VoiceSource, VoiceSourceKind


PROFILE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class EngineVoiceBinding:
    """A calibrated way to reproduce one voice on one engine.

    The public VoiceProfile stays engine-independent. Bindings live beside it and
    may evolve independently as engines, models and recipes are certified.
    """

    engine_id: str
    model_id: str | None = None
    model_revision: str | None = None
    recipe_id: str | None = None
    recipe_revision: int | None = None
    style_recipes: dict[str, str] = field(default_factory=dict)
    engine_voice_id: str | None = None
    prompt_artifact: ArtifactRef | None = None
    certified_languages: tuple[str, ...] = ()
    quality_score: float | None = None
    speaker_similarity_score: float | None = None
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = PROFILE_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class StoredVoiceProfile:
    profile: VoiceProfile
    bindings: tuple[EngineVoiceBinding, ...] = ()
    pronunciation_hints: dict[str, str] = field(default_factory=dict)
    preferred_styles: tuple[str, ...] = ("natural", "creator")
    created_at: str = ""
    updated_at: str = ""
    schema_version: int = PROFILE_SCHEMA_VERSION

    def binding_for(self, engine_id: str) -> EngineVoiceBinding | None:
        return next((binding for binding in self.bindings if binding.engine_id == engine_id and binding.enabled), None)


class VoiceProfileStore:
    """Local, versioned profile store shared by Studio clients.

    Profiles contain no ACE account names or publishing metadata. An external
    orchestrator stores its own account -> profile_id mapping.
    """

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_id(value: str) -> str:
        clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", (value or "voice").strip()).strip("-._").lower()
        return clean or "voice"

    def path_for(self, profile_id: str) -> Path:
        return self.directory / f"{self._safe_id(profile_id)}.json"

    def list_ids(self) -> tuple[str, ...]:
        return tuple(sorted(path.stem for path in self.directory.glob("*.json") if path.is_file()))

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
    def _profile_from_dict(cls, value: dict[str, Any]) -> VoiceProfile:
        row = dict(value or {})
        row["source"] = cls._source_from_dict(row.get("source") or {})
        if isinstance(row.get("canonical_reference"), dict):
            row["canonical_reference"] = cls._artifact_from_dict(row["canonical_reference"])
        if isinstance(row.get("supported_languages"), list):
            row["supported_languages"] = tuple(row["supported_languages"])
        if isinstance(row.get("preferred_languages"), list):
            row["preferred_languages"] = tuple(row["preferred_languages"])
        return VoiceProfile(**row)

    @classmethod
    def _binding_from_dict(cls, value: dict[str, Any]) -> EngineVoiceBinding:
        row = dict(value or {})
        if isinstance(row.get("prompt_artifact"), dict):
            row["prompt_artifact"] = cls._artifact_from_dict(row["prompt_artifact"])
        if isinstance(row.get("certified_languages"), list):
            row["certified_languages"] = tuple(row["certified_languages"])
        return EngineVoiceBinding(**row)

    @classmethod
    def _decode(cls, payload: dict[str, Any]) -> StoredVoiceProfile:
        if int(payload.get("schema_version", 0)) != PROFILE_SCHEMA_VERSION:
            raise ValueError("Unsupported voice profile schema version.")
        return StoredVoiceProfile(
            profile=cls._profile_from_dict(payload["profile"]),
            bindings=tuple(cls._binding_from_dict(item) for item in payload.get("bindings", [])),
            pronunciation_hints=dict(payload.get("pronunciation_hints") or {}),
            preferred_styles=tuple(payload.get("preferred_styles") or ("natural", "creator")),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            schema_version=PROFILE_SCHEMA_VERSION,
        )

    @staticmethod
    def _encode(record: StoredVoiceProfile) -> dict[str, Any]:
        def clean(value: Any) -> Any:
            if isinstance(value, tuple):
                return [clean(item) for item in value]
            if isinstance(value, list):
                return [clean(item) for item in value]
            if isinstance(value, dict):
                return {str(key): clean(item) for key, item in value.items()}
            if hasattr(value, "value"):
                return value.value
            return value

        return clean(asdict(record))

    def get(self, profile_id: str) -> StoredVoiceProfile | None:
        path = self.path_for(profile_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Voice profile '{profile_id}' is unreadable.") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Voice profile '{profile_id}' is invalid.")
        return self._decode(payload)

    def save(self, record: StoredVoiceProfile) -> StoredVoiceProfile:
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get(record.profile.profile_id)
        created_at = record.created_at or (existing.created_at if existing else now)
        normalized = StoredVoiceProfile(
            profile=record.profile,
            bindings=record.bindings,
            pronunciation_hints=dict(record.pronunciation_hints),
            preferred_styles=tuple(record.preferred_styles),
            created_at=created_at,
            updated_at=now,
            schema_version=PROFILE_SCHEMA_VERSION,
        )
        path = self.path_for(record.profile.profile_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._encode(normalized), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
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
        preferred_languages: tuple[str, ...] = (),
        consistency_locked: bool = True,
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
            consistency_locked=bool(consistency_locked),
            supported_languages=tuple(supported_languages),
            preferred_languages=tuple(preferred_languages),
            canonical_reference=reference,
        )
        return self.save(StoredVoiceProfile(profile=profile))

    def add_binding(self, profile_id: str, binding: EngineVoiceBinding, *, promote_revision: bool = False) -> StoredVoiceProfile:
        record = self.get(profile_id)
        if record is None:
            raise FileNotFoundError(f"Voice profile '{profile_id}' does not exist.")
        bindings = [item for item in record.bindings if item.engine_id != binding.engine_id]
        bindings.append(binding)
        profile = record.profile
        if promote_revision:
            profile = VoiceProfile(
                profile_id=profile.profile_id,
                display_name=profile.display_name,
                source=profile.source,
                revision=profile.revision + 1,
                consistency_locked=profile.consistency_locked,
                supported_languages=profile.supported_languages,
                preferred_languages=profile.preferred_languages,
                canonical_reference=profile.canonical_reference,
                metadata=dict(profile.metadata),
                schema_version=profile.schema_version,
            )
        return self.save(
            StoredVoiceProfile(
                profile=profile,
                bindings=tuple(sorted(bindings, key=lambda item: item.engine_id)),
                pronunciation_hints=record.pronunciation_hints,
                preferred_styles=record.preferred_styles,
                created_at=record.created_at,
            )
        )

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
        return self.save(
            StoredVoiceProfile(
                profile=record.profile,
                bindings=record.bindings,
                pronunciation_hints=hints,
                preferred_styles=record.preferred_styles,
                created_at=record.created_at,
            )
        )

    def delete(self, profile_id: str) -> bool:
        path = self.path_for(profile_id)
        if not path.exists():
            return False
        path.unlink()
        return True
