from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

from studio.artifact_store import ArtifactStore
from studio.naming import safe_local_name
from studio.protocol import ArtifactRef, EngineBinding, VoiceProfile
from studio.voice_profile_store import StoredVoiceProfile, VoiceProfileStore


VOICEPACK_FORMAT = "creator-studio.voicepack"
VOICEPACK_VERSION = 1
MAX_PACK_FILES = 64
MAX_PACK_UNCOMPRESSED_BYTES = 2 * 1024**3


class VoicePackError(ValueError):
    pass


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _owned_artifacts(profile: VoiceProfile) -> tuple[ArtifactRef, ...]:
    rows: dict[str, ArtifactRef] = {}

    def add(ref: ArtifactRef | None) -> None:
        if ref is None:
            return
        previous = rows.get(ref.artifact_id)
        if previous is not None and previous.to_dict() != ref.to_dict():
            raise VoicePackError(f"Voice profile reuses artifact ID '{ref.artifact_id}' with conflicting metadata.")
        rows[ref.artifact_id] = ref

    add(profile.source.reference)
    for binding in profile.engine_bindings:
        add(binding.prompt_artifact)
    return tuple(rows[key] for key in sorted(rows))


def _replace_artifacts(profile: VoiceProfile, mapping: dict[str, ArtifactRef]) -> VoiceProfile:
    source = profile.source
    if source.reference is not None:
        source = replace(source, reference=mapping[source.reference.artifact_id])

    bindings: list[EngineBinding] = []
    for binding in profile.engine_bindings:
        prompt = binding.prompt_artifact
        if prompt is not None:
            prompt = mapping[prompt.artifact_id]
        bindings.append(replace(binding, prompt_artifact=prompt))
    return replace(profile, source=source, engine_bindings=tuple(bindings))


def export_voice_pack(
    profile_id: str,
    *,
    profile_store: VoiceProfileStore,
    artifact_store: ArtifactStore,
    destination: str | Path,
) -> Path:
    """Export one portable voice identity without model/runtime files."""

    record = profile_store.get(profile_id)
    if record is None:
        raise FileNotFoundError(f"Voice profile '{profile_id}' does not exist.")

    destination_path = Path(destination).expanduser().resolve()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict[str, Any]] = []
    resolved: list[tuple[str, Path]] = []
    for index, ref in enumerate(_owned_artifacts(record.profile), start=1):
        path = artifact_store.resolve(ref)
        suffix = path.suffix.lower()
        member = f"artifacts/{index:02d}-{safe_local_name(ref.artifact_id, fallback='artifact', casefold=True)}{suffix}"
        artifacts.append({"ref": ref.to_dict(), "member": member})
        resolved.append((member, path))

    manifest = {
        "format": VOICEPACK_FORMAT,
        "format_version": VOICEPACK_VERSION,
        "profile": record.profile.to_dict(),
        "created_at": record.created_at,
        "exported_artifacts": artifacts,
    }

    tmp = destination_path.with_suffix(destination_path.suffix + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for member, path in resolved:
                archive.write(path, member)
        tmp.replace(destination_path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return destination_path


def import_voice_pack(
    source: str | Path,
    *,
    profile_store: VoiceProfileStore,
    artifact_store: ArtifactStore,
    profile_id_override: str | None = None,
) -> StoredVoiceProfile:
    """Import a Voice Pack with validation and rollback of newly-created artifacts.

    The archive is never extracted wholesale. Only manifest-declared members are read,
    hashes are verified before registration, the profile is written last, and artifacts
    created by a failed import are removed again.
    """

    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError("Voice Pack not found.")

    created_refs: list[ArtifactRef] = []
    try:
        with zipfile.ZipFile(source_path, "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_PACK_FILES:
                raise VoicePackError("Voice Pack contains too many files.")
            if sum(info.file_size for info in infos) > MAX_PACK_UNCOMPRESSED_BYTES:
                raise VoicePackError("Voice Pack is too large when unpacked.")
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise VoicePackError("Voice Pack contains duplicate archive members.")
            if names.count("manifest.json") != 1:
                raise VoicePackError("Voice Pack must contain exactly one manifest.json.")
            try:
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
                raise VoicePackError("Voice Pack manifest is invalid.") from exc
            if not isinstance(manifest, dict):
                raise VoicePackError("Voice Pack manifest is invalid.")
            try:
                format_version = int(manifest.get("format_version", 0))
            except (TypeError, ValueError) as exc:
                raise VoicePackError("Voice Pack format version is invalid.") from exc
            if manifest.get("format") != VOICEPACK_FORMAT or format_version != VOICEPACK_VERSION:
                raise VoicePackError("Unsupported Voice Pack format/version.")

            try:
                profile = profile_store._profile_from_dict(manifest["profile"])
            except (KeyError, TypeError, ValueError) as exc:
                raise VoicePackError("Voice Pack profile is invalid.") from exc

            target_id = profile_store._safe_id(profile_id_override or profile.profile_id)
            if profile_store.get(target_id) is not None:
                raise FileExistsError(f"Voice profile '{target_id}' already exists.")

            exported = manifest.get("exported_artifacts") or []
            if not isinstance(exported, list):
                raise VoicePackError("Voice Pack artifact manifest is invalid.")
            expected_names = {"manifest.json"}
            rows: list[tuple[ArtifactRef, str]] = []
            seen_artifact_ids: set[str] = set()
            for item in exported:
                if not isinstance(item, dict) or not isinstance(item.get("ref"), dict) or not isinstance(item.get("member"), str):
                    raise VoicePackError("Voice Pack artifact entry is invalid.")
                try:
                    ref = ArtifactRef(**item["ref"])
                except TypeError as exc:
                    raise VoicePackError("Voice Pack artifact reference is invalid.") from exc
                member = item["member"]
                posix = PurePosixPath(member)
                if (
                    member == "manifest.json"
                    or not member.startswith("artifacts/")
                    or member != posix.as_posix()
                    or posix.is_absolute()
                    or ".." in posix.parts
                    or len(posix.parts) != 2
                ):
                    raise VoicePackError("Voice Pack contains an unsafe artifact member.")
                if member in expected_names or ref.artifact_id in seen_artifact_ids:
                    raise VoicePackError("Voice Pack contains duplicate artifact entries.")
                expected_names.add(member)
                seen_artifact_ids.add(ref.artifact_id)
                rows.append((ref, member))

            if set(names) != expected_names:
                raise VoicePackError("Voice Pack contains unexpected or missing files.")

            required_ids = {ref.artifact_id for ref in _owned_artifacts(profile)}
            if required_ids != seen_artifact_ids:
                raise VoicePackError("Voice Pack artifact list does not match the profile references.")

            mapping: dict[str, ArtifactRef] = {}
            with tempfile.TemporaryDirectory(prefix="voicepack-") as temp_directory:
                stage = Path(temp_directory)
                for index, (ref, member) in enumerate(rows, start=1):
                    data = archive.read(member)
                    if ref.size_bytes is not None and len(data) != ref.size_bytes:
                        raise VoicePackError(f"Voice Pack artifact '{ref.artifact_id}' has the wrong size.")
                    digest = _sha256_bytes(data)
                    if ref.sha256 and digest != ref.sha256:
                        raise VoicePackError(f"Voice Pack artifact '{ref.artifact_id}' failed its integrity check.")
                    suffix = PurePosixPath(member).suffix.lower()
                    staged = stage / f"{index:02d}{suffix}"
                    staged.write_bytes(data)

                    safe_id = artifact_store._safe_id(ref.artifact_id)
                    existed_before = bool(artifact_store._paths_for_id(safe_id))
                    imported = artifact_store.register_file(
                        staged,
                        artifact_id=ref.artifact_id,
                        mime_type=ref.mime_type,
                        copy=True,
                    )
                    if not existed_before:
                        created_refs.append(imported)
                    mapping[ref.artifact_id] = imported

            profile = _replace_artifacts(profile, mapping)
            profile = replace(profile, profile_id=target_id)
            saved = profile_store.save(
                StoredVoiceProfile(
                    profile=profile,
                    created_at=str(manifest.get("created_at") or ""),
                )
            )
            created_refs.clear()
            return saved
    except (zipfile.BadZipFile, KeyError) as exc:
        raise VoicePackError("Voice Pack archive is invalid.") from exc
    except Exception:
        for ref in reversed(created_refs):
            try:
                artifact_store.remove(ref)
            except (FileNotFoundError, ValueError):
                pass
        raise
