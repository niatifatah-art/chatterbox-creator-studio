from __future__ import annotations

import array
import json
import math
import re
import shutil
import wave
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class VoiceProfile:
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


class VoiceLibrary:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _slug(name: str) -> str:
        # Keep Unicode letters/digits (Arabic, CJK, etc.) while removing path
        # separators and unsafe punctuation from local filenames.
        clean = re.sub(r"[^\w.-]+", "-", (name or "voice").strip(), flags=re.UNICODE).strip("-._")
        return clean or "voice"

    def list(self) -> list[str]:
        return sorted(path.stem for path in self.directory.glob("*.wav") if path.is_file())

    def path_for(self, name: str | None) -> Path | None:
        if not name:
            return None
        candidate = self.directory / f"{self._slug(name)}.wav"
        return candidate if candidate.exists() else None

    def metadata_path(self, name: str) -> Path:
        return self.directory / f"{self._slug(name)}.json"

    def inspect(self, wav_path: str | Path, name: str | None = None) -> VoiceProfile:
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
                return VoiceProfile(
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
            return VoiceProfile(
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
            return VoiceProfile(name=profile_name, path=str(path), warning=f"Could not fully inspect reference: {exc}")

    def _write_metadata(self, profile: VoiceProfile) -> None:
        payload = asdict(profile)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        destination = self.metadata_path(profile.name)
        tmp = destination.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(destination)

    def profile(self, name: str | None) -> VoiceProfile | None:
        path = self.path_for(name)
        if path is None:
            return None
        metadata = self.metadata_path(path.stem)
        if metadata.exists():
            try:
                payload = json.loads(metadata.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    allowed = {field.name for field in VoiceProfile.__dataclass_fields__.values()}
                    values = {key: value for key, value in payload.items() if key in allowed}
                    return VoiceProfile(**values)
            except Exception:
                pass
        profile = self.inspect(path, path.stem)
        self._write_metadata(profile)
        return profile

    def profiles(self) -> list[VoiceProfile]:
        return [profile for name in self.list() if (profile := self.profile(name)) is not None]

    def save(self, uploaded_wav: str | None, display_name: str | None) -> tuple[str, Path]:
        if not uploaded_wav:
            raise ValueError("Choose or record a reference voice first.")
        source = Path(uploaded_wav)
        if not source.exists():
            raise FileNotFoundError("The uploaded reference audio is no longer available.")
        slug = self._slug(display_name or source.stem)
        destination = self.directory / f"{slug}.wav"
        shutil.copyfile(source, destination)
        profile = self.inspect(destination, slug)
        self._write_metadata(profile)
        return slug, destination

    def rename(self, name: str, new_name: str) -> str:
        source = self.path_for(name)
        if source is None:
            raise FileNotFoundError("Voice profile not found.")
        slug = self._slug(new_name)
        destination = self.directory / f"{slug}.wav"
        if destination.exists() and destination != source:
            raise FileExistsError("A voice with that name already exists.")
        source.replace(destination)
        old_meta = self.metadata_path(name)
        if old_meta.exists():
            old_meta.unlink()
        self._write_metadata(self.inspect(destination, slug))
        return slug

    def duplicate(self, name: str, new_name: str | None = None) -> str:
        source = self.path_for(name)
        if source is None:
            raise FileNotFoundError("Voice profile not found.")
        base = self._slug(new_name or f"{name}-copy")
        slug = base
        index = 2
        while (self.directory / f"{slug}.wav").exists():
            slug = f"{base}-{index}"
            index += 1
        destination = self.directory / f"{slug}.wav"
        shutil.copy2(source, destination)
        self._write_metadata(self.inspect(destination, slug))
        return slug

    def delete(self, name: str) -> bool:
        path = self.path_for(name)
        if path is None:
            return False
        path.unlink()
        metadata = self.metadata_path(name)
        if metadata.exists():
            metadata.unlink()
        return True
