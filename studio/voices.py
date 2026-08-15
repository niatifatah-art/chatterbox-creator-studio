from __future__ import annotations

import re
import shutil
from pathlib import Path


class VoiceLibrary:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _slug(name: str) -> str:
        clean = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "voice").strip()).strip("-._")
        return clean or "voice"

    def list(self) -> list[str]:
        return sorted(path.stem for path in self.directory.glob("*.wav") if path.is_file())

    def path_for(self, name: str | None) -> Path | None:
        if not name:
            return None
        candidate = self.directory / f"{self._slug(name)}.wav"
        return candidate if candidate.exists() else None

    def save(self, uploaded_wav: str | None, display_name: str | None) -> tuple[str, Path]:
        if not uploaded_wav:
            raise ValueError("Choose or record a reference voice first.")
        source = Path(uploaded_wav)
        if not source.exists():
            raise FileNotFoundError("The uploaded reference audio is no longer available.")
        slug = self._slug(display_name or source.stem)
        destination = self.directory / f"{slug}.wav"
        shutil.copyfile(source, destination)
        return slug, destination
