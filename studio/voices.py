from __future__ import annotations

import array
import hashlib
import json
import math
import re
import shutil
import unicodedata
import wave
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from studio.artifact_store import ArtifactStore
from studio.protocol import VoiceSource, VoiceSourceKind
from studio.voice_profile_store import StoredVoiceProfile, VoiceProfileStore


@dataclass(frozen=True)
class VoiceReferenceAnalysis:
    """Advisory analysis of one reference file, not the durable voice identity."""

    name: str
    path: str
    duration_seconds: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    peak: float | None = None
    rms: float | None = None
    silence_ratio: float | None = None
    clipping_ratio: float | None = None
    warning: str | None = None


# Compatibility alias for any early code that imported the old analysis dataclass by
# this misleading name. New code should use protocol.VoiceProfile for voice identity.
VoiceProfile = VoiceReferenceAnalysis


class VoiceLibrary:
    """Compatibility facade for the working Chatterbox UI during voice-data migration.

    Canonical identity/metadata lives in Speech Core's `VoiceProfileStore`. The legacy
    `data/voices/*.wav` files remain as a temporary mirror because the current direct
    Chatterbox controller still consumes file paths. Phase 2 removes that requirement
    after synthesis moves behind Speech Core.
    """

    def __init__(self, directory: str | Path, core_directory: str | Path | None = None):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.core_directory = (
            Path(core_directory).expanduser().resolve()
            if core_directory is not None
            else (self.directory.parent / "speech-core").resolve()
        )
        self.core_directory.mkdir(parents=True, exist_ok=True)
        self.profile_store = VoiceProfileStore(self.core_directory / "voice-profiles")
        self.artifact_store = ArtifactStore(self.core_directory / "artifacts")
        self.backup_directory = self.core_directory / "backups" / "legacy-voices-v1"
        self.migration_warnings: list[str] = []
        self._migrate_legacy_files()

    @staticmethod
    def _slug(name: str) -> str:
        # Keep Unicode letters/digits (Arabic, CJK, etc.) while removing path
        # separators and unsafe punctuation from local filenames.
        normalized = unicodedata.normalize("NFKC", (name or "voice").strip())
        clean = re.sub(r"[^\w.-]+", "-", normalized, flags=re.UNICODE).strip("-._")
        return clean or "voice"

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _artifact_id(self, slug: str, path: Path) -> str:
        # Per-profile IDs avoid reference-count problems when Duplicate creates another
        # voice with identical audio. The content hash keeps migration idempotent and
        # gives a new immutable artifact when a reference is deliberately replaced.
        return f"voice-ref-{self.profile_store._safe_id(slug)}-{self._sha256(path)}"

    def _legacy_path(self, slug: str) -> Path:
        return self.directory / f"{slug}.wav"

    def metadata_path(self, name: str) -> Path:
        return self.directory / f"{self._slug(name)}.json"

    def _backup_legacy_pair(self, slug: str) -> None:
        wav = self._legacy_path(slug)
        meta = self.metadata_path(slug)
        if not wav.exists() and not meta.exists():
            return
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        for source in (wav, meta):
            if not source.exists():
                continue
            destination = self.backup_directory / source.name
            if not destination.exists():
                shutil.copy2(source, destination)

    @staticmethod
    def _analysis_payload(profile: VoiceReferenceAnalysis) -> dict[str, Any]:
        payload = asdict(profile)
        payload.pop("name", None)
        payload.pop("path", None)  # never persist an absolute user path in Speech Core
        return payload

    def _metadata_for(self, slug: str, analysis: VoiceReferenceAnalysis, *, migration_source: str) -> dict[str, Any]:
        return {
            "legacy_slug": slug,
            "reference_analysis": self._analysis_payload(analysis),
            "migration_source": migration_source,
        }

    def _record_for_name(self, name: str | None):
        if not name:
            return None
        slug = self._slug(name)
        exact_legacy = []
        fallback = []
        for record in self.profile_store.list():
            profile = record.profile
            if profile.source.kind != VoiceSourceKind.CLONE or profile.source.reference is None:
                continue
            legacy_slug = self._slug(str(profile.metadata.get("legacy_slug") or "")) if profile.metadata.get("legacy_slug") else ""
            if legacy_slug == slug:
                exact_legacy.append(record)
                continue
            if self._slug(profile.display_name) == slug or self.profile_store._safe_id(profile.profile_id) == self.profile_store._safe_id(slug):
                fallback.append(record)
        return (exact_legacy or fallback or [None])[0]

    def _register_reference(self, slug: str, path: Path):
        return self.artifact_store.register_file(
            path,
            artifact_id=self._artifact_id(slug, path),
            mime_type="audio/wav",
            copy=True,
        )

    def _legacy_analysis_from_json(self, slug: str, path: Path) -> VoiceReferenceAnalysis | None:
        metadata = self.metadata_path(slug)
        if not metadata.exists():
            return None
        try:
            payload = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        allowed = set(VoiceReferenceAnalysis.__dataclass_fields__)
        values = {key: value for key, value in payload.items() if key in allowed}
        values["name"] = slug
        values["path"] = str(path)
        try:
            return VoiceReferenceAnalysis(**values)
        except TypeError:
            return None

    def _analysis_from_record(self, record, path: Path) -> VoiceReferenceAnalysis | None:
        raw = record.profile.metadata.get("reference_analysis")
        if not isinstance(raw, dict):
            return None
        allowed = set(VoiceReferenceAnalysis.__dataclass_fields__) - {"name", "path"}
        values = {key: value for key, value in raw.items() if key in allowed}
        try:
            return VoiceReferenceAnalysis(name=record.profile.display_name, path=str(path), **values)
        except TypeError:
            return None

    def _migrate_legacy_file(self, path: Path) -> None:
        slug = path.stem
        self._backup_legacy_pair(slug)
        analysis = self._legacy_analysis_from_json(slug, path) or self.inspect(path, slug)
        artifact = self._register_reference(slug, path)

        existing = self._record_for_name(slug)
        if existing is not None:
            current = existing.profile.source.reference
            if current is not None and current.sha256 == artifact.sha256:
                if not isinstance(existing.profile.metadata.get("reference_analysis"), dict):
                    metadata = dict(existing.profile.metadata)
                    metadata.update(self._metadata_for(slug, analysis, migration_source="voice-library-v1"))
                    self.profile_store.update_metadata(existing.profile.profile_id, metadata)
                return

        base_id = self.profile_store._safe_id(slug)
        profile_id = base_id
        occupied = self.profile_store.get(profile_id)
        if occupied is not None:
            current = occupied.profile.source.reference
            if current is not None and current.sha256 == artifact.sha256:
                return
            profile_id = f"{base_id}-{(artifact.sha256 or 'imported')[:8]}"
            second = self.profile_store.get(profile_id)
            if second is not None:
                current = second.profile.source.reference
                if current is not None and current.sha256 == artifact.sha256:
                    return
                raise FileExistsError(f"Voice migration conflict for '{slug}'.")

        self.profile_store.create(
            profile_id,
            slug,
            source_kind=VoiceSourceKind.CLONE,
            reference=artifact,
            default_style="creator",
            consistency_locked=True,
            metadata=self._metadata_for(slug, analysis, migration_source="voice-library-v1"),
        )

    def _migrate_legacy_files(self) -> None:
        for path in sorted(self.directory.glob("*.wav")):
            if not path.is_file():
                continue
            try:
                self._migrate_legacy_file(path)
            except Exception as exc:
                # Never hide a user's old voice merely because the new index could not
                # migrate it. The legacy mirror remains usable and the warning can be
                # surfaced by diagnostics during the transition.
                self.migration_warnings.append(f"{path.name}: {exc}")

    def list(self) -> list[str]:
        names: set[str] = set()
        for record in self.profile_store.list():
            if record.profile.source.kind == VoiceSourceKind.CLONE and record.profile.source.reference is not None:
                names.add(record.profile.display_name)
        # Compatibility fallback if a corrupt/conflicting legacy entry could not be
        # migrated; Phase 1 must not make an existing voice disappear from the UI.
        names.update(path.stem for path in self.directory.glob("*.wav") if path.is_file())
        return sorted(names)

    def path_for(self, name: str | None) -> Path | None:
        if not name:
            return None
        slug = self._slug(name)
        legacy = self._legacy_path(slug)
        if legacy.exists():
            return legacy
        record = self._record_for_name(name)
        if record is None or record.profile.source.reference is None:
            return None
        try:
            return self.artifact_store.resolve(record.profile.source.reference)
        except (FileNotFoundError, ValueError):
            return None

    def inspect(self, wav_path: str | Path, name: str | None = None) -> VoiceReferenceAnalysis:
        path = Path(wav_path)
        profile_name = self._slug(name or path.stem)
        if not path.exists():
            raise FileNotFoundError("Voice file not found.")
        try:
            with wave.open(str(path), "rb") as handle:
                channels = int(handle.getnchannels())
                sample_rate = int(handle.getframerate())
                sample_width = int(handle.getsampwidth())
                frames = int(handle.getnframes())
                raw = handle.readframes(frames)
            duration = frames / float(sample_rate) if sample_rate else 0.0
            if sample_width != 2:
                return VoiceReferenceAnalysis(
                    name=profile_name,
                    path=str(path),
                    duration_seconds=duration,
                    sample_rate=sample_rate,
                    channels=channels,
                    warning="Reference analysis is limited for non-16-bit PCM WAV files.",
                )
            values = array.array("h")
            values.frombytes(raw)
            if not values:
                raise ValueError("Reference audio is empty.")
            max_int = 32768.0
            absolute = [abs(value) / max_int for value in values]
            peak = max(absolute)
            rms = math.sqrt(sum((value / max_int) ** 2 for value in values) / len(values))
            silence_ratio = sum(1 for value in absolute if value <= 0.004) / len(absolute)
            clipping_ratio = sum(1 for value in absolute if value >= 0.995) / len(absolute)
            warnings: list[str] = []
            if duration <= 5.0:
                warnings.append("This sample is too short for some models; use more than 5 seconds, ideally about 8–15 seconds of clean speech.")
            elif duration < 8.0:
                warnings.append("This sample can work, but about 8–15 seconds of clean speech is usually safer.")
            if duration > 45.0:
                warnings.append("This sample is long; a shorter clean excerpt is easier to manage.")
            if silence_ratio > 0.55:
                warnings.append("This sample contains a lot of silence.")
            if clipping_ratio > 0.001:
                warnings.append("This sample may be clipping.")
            if rms < 0.01:
                warnings.append("This sample is very quiet.")
            return VoiceReferenceAnalysis(
                name=profile_name,
                path=str(path),
                duration_seconds=duration,
                sample_rate=sample_rate,
                channels=channels,
                peak=peak,
                rms=rms,
                silence_ratio=silence_ratio,
                clipping_ratio=clipping_ratio,
                warning=" ".join(warnings) or None,
            )
        except (wave.Error, EOFError, ValueError) as exc:
            return VoiceReferenceAnalysis(name=profile_name, path=str(path), warning=f"Could not fully inspect reference: {exc}")

    def _write_metadata(self, profile: VoiceReferenceAnalysis) -> None:
        # Temporary legacy mirror only. Canonical Speech Core metadata deliberately
        # omits the absolute `path` field.
        payload = asdict(profile)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        destination = self.metadata_path(profile.name)
        tmp = destination.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(destination)

    def _sync_analysis(self, record, slug: str, analysis: VoiceReferenceAnalysis) -> None:
        metadata = dict(record.profile.metadata)
        metadata.update(self._metadata_for(slug, analysis, migration_source=str(metadata.get("migration_source") or "voice-library-facade")))
        self.profile_store.update_metadata(record.profile.profile_id, metadata)

    def profile(self, name: str | None) -> VoiceReferenceAnalysis | None:
        path = self.path_for(name)
        if path is None:
            return None
        record = self._record_for_name(name)
        if record is not None:
            analysis = self._analysis_from_record(record, path)
            if analysis is not None:
                return analysis
        slug = self._slug(name or path.stem)
        analysis = self._legacy_analysis_from_json(slug, path) or self.inspect(path, slug)
        self._write_metadata(analysis)
        if record is not None:
            self._sync_analysis(record, slug, analysis)
        else:
            try:
                self._migrate_legacy_file(path)
            except Exception as exc:
                self.migration_warnings.append(f"{path.name}: {exc}")
        return analysis

    def profiles(self) -> list[VoiceReferenceAnalysis]:
        return [profile for name in self.list() if (profile := self.profile(name)) is not None]

    def save(self, uploaded_wav: str | None, display_name: str | None) -> tuple[str, Path]:
        if not uploaded_wav:
            raise ValueError("Choose or record a reference voice first.")
        source = Path(uploaded_wav).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError("The uploaded reference audio is no longer available.")
        slug = self._slug(display_name or source.stem)
        destination = self._legacy_path(slug)
        if destination.exists() and destination.resolve() != source:
            self._backup_legacy_pair(slug)
        if destination.resolve() != source:
            shutil.copy2(source, destination)
        analysis = self.inspect(destination, slug)
        self._write_metadata(analysis)
        artifact = self._register_reference(slug, destination)
        metadata = self._metadata_for(slug, analysis, migration_source="voice-library-facade")

        record = self._record_for_name(slug)
        if record is None:
            self.profile_store.create(
                slug,
                slug,
                source_kind=VoiceSourceKind.CLONE,
                reference=artifact,
                default_style="creator",
                consistency_locked=True,
                metadata=metadata,
            )
        else:
            profile = replace(
                record.profile,
                display_name=slug,
                source=VoiceSource(kind=VoiceSourceKind.CLONE, reference=artifact),
                revision=record.profile.revision + 1,
                metadata=metadata,
            )
            self.profile_store.save(StoredVoiceProfile(profile=profile, created_at=record.created_at))
        return slug, destination

    def rename(self, name: str, new_name: str) -> str:
        source = self.path_for(name)
        if source is None:
            raise FileNotFoundError("Voice profile not found.")
        old_slug = self._slug(name)
        slug = self._slug(new_name)
        destination = self._legacy_path(slug)
        legacy_source = self._legacy_path(old_slug)
        if destination.exists() and destination != legacy_source:
            raise FileExistsError("A voice with that name already exists.")

        record = self._record_for_name(name)
        if legacy_source.exists() and destination != legacy_source:
            legacy_source.replace(destination)
        old_meta = self.metadata_path(old_slug)
        new_meta = self.metadata_path(slug)
        if old_meta.exists() and old_meta != new_meta:
            if new_meta.exists():
                new_meta.unlink()
            old_meta.replace(new_meta)

        if record is None:
            self._migrate_legacy_file(destination if destination.exists() else source)
            record = self._record_for_name(slug)
        if record is not None:
            metadata = dict(record.profile.metadata)
            metadata["legacy_slug"] = slug
            profile = replace(record.profile, display_name=slug, metadata=metadata)
            self.profile_store.save(StoredVoiceProfile(profile=profile, created_at=record.created_at))

        # Refresh the temporary mirror's advisory metadata with the new display name.
        actual = destination if destination.exists() else source
        self._write_metadata(self.inspect(actual, slug))
        return slug

    def duplicate(self, name: str, new_name: str | None = None) -> str:
        source = self.path_for(name)
        if source is None:
            raise FileNotFoundError("Voice profile not found.")
        base = self._slug(new_name or f"{name}-copy")
        slug = base
        index = 2
        existing_names = set(self.list())
        while slug in existing_names or self._legacy_path(slug).exists():
            slug = f"{base}-{index}"
            index += 1
        destination = self._legacy_path(slug)
        shutil.copy2(source, destination)
        analysis = self.inspect(destination, slug)
        self._write_metadata(analysis)
        artifact = self._register_reference(slug, destination)
        self.profile_store.create(
            slug,
            slug,
            source_kind=VoiceSourceKind.CLONE,
            reference=artifact,
            default_style="creator",
            consistency_locked=True,
            metadata=self._metadata_for(slug, analysis, migration_source="voice-library-duplicate"),
        )
        return slug

    def delete(self, name: str) -> bool:
        slug = self._slug(name)
        record = self._record_for_name(name)
        legacy = self._legacy_path(slug)
        existed = bool(record is not None or legacy.exists())
        if legacy.exists():
            legacy.unlink()
        metadata = self.metadata_path(slug)
        if metadata.exists():
            metadata.unlink()
        if record is not None:
            reference = record.profile.source.reference
            self.profile_store.delete(record.profile.profile_id)
            if reference is not None:
                try:
                    self.artifact_store.remove(reference)
                except ValueError:
                    # Integrity mismatch should not make the profile undeletable. The
                    # suspicious artifact is intentionally left for diagnostics/repair.
                    pass
        return existed
