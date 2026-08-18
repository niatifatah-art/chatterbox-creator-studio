from __future__ import annotations

import hashlib
import mimetypes
import re
import shutil
import uuid
from pathlib import Path

from studio.protocol import ArtifactRef


class ArtifactStore:
    """Small local artifact resolver for durable speech contracts.

    Contracts receive `local://artifacts/<id>` references instead of absolute user
    paths. The real file location remains an implementation detail of this store.
    """

    URI_PREFIX = "local://artifacts/"

    def __init__(self, directory: str | Path):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_id(value: str | None) -> str:
        if value:
            clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-._").lower()
            if clean:
                return clean
        return uuid.uuid4().hex

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def register_file(
        self,
        source: str | Path,
        *,
        artifact_id: str | None = None,
        mime_type: str | None = None,
        copy: bool = True,
    ) -> ArtifactRef:
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"Artifact source does not exist: {source_path.name}")

        safe_id = self._safe_id(artifact_id)
        suffix = source_path.suffix.lower()
        destination = (self.directory / f"{safe_id}{suffix}").resolve()
        if self.directory not in destination.parents:
            raise ValueError("Artifact destination escaped the local artifact store.")

        source_hash = self._sha256(source_path)
        if copy:
            if destination.exists() and destination != source_path:
                # Deterministic IDs are useful for idempotent migrations, but they must
                # never turn into an accidental overwrite primitive.
                if self._sha256(destination) != source_hash:
                    raise FileExistsError(f"Artifact ID '{safe_id}' already refers to different content.")
            elif destination != source_path:
                shutil.copy2(source_path, destination)
        else:
            if self.directory not in source_path.parents:
                raise ValueError("Non-copy registration is allowed only for files already inside the artifact store.")
            destination = source_path
            safe_id = destination.stem

        detected_mime = mime_type or mimetypes.guess_type(destination.name)[0] or "application/octet-stream"
        return ArtifactRef(
            artifact_id=safe_id,
            mime_type=detected_mime,
            uri=f"{self.URI_PREFIX}{safe_id}",
            size_bytes=destination.stat().st_size,
            sha256=source_hash,
        )

    def resolve(self, artifact: ArtifactRef) -> Path:
        if not artifact.uri.startswith(self.URI_PREFIX):
            raise ValueError("This store can resolve only local://artifacts references.")
        identifier = artifact.uri[len(self.URI_PREFIX) :]
        if not identifier or "/" in identifier or "\\" in identifier or identifier in {".", ".."}:
            raise ValueError("Invalid local artifact identifier.")
        safe_id = self._safe_id(identifier)
        candidates = sorted(self.directory.glob(f"{safe_id}.*"))
        if not candidates:
            # Extensionless artifacts are valid too.
            direct = (self.directory / safe_id).resolve()
            if direct.is_file() and self.directory in direct.parents:
                candidates = [direct]
        if not candidates:
            raise FileNotFoundError(f"Local artifact '{safe_id}' is missing.")
        path = candidates[0].resolve()
        if self.directory not in path.parents:
            raise ValueError("Resolved artifact escaped the local artifact store.")
        if artifact.sha256 and self._sha256(path) != artifact.sha256:
            raise ValueError(f"Local artifact '{safe_id}' failed its integrity check.")
        return path

    def remove(self, artifact: ArtifactRef) -> bool:
        try:
            path = self.resolve(artifact)
        except FileNotFoundError:
            return False
        path.unlink()
        return True
