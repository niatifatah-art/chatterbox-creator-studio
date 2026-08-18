from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


SCHEMA_VERSION = 1


class Capability(str, Enum):
    SYNTHESIZE = "speech.synthesize.v1"
    TRANSCRIBE = "speech.transcribe.v1"
    ALIGN = "speech.align.v1"
    VOICE_CLONE = "speech.voice.clone.v1"
    VOICE_DESIGN = "speech.voice.design.v1"
    READY_VOICE = "speech.voice.ready.v1"
    REFERENCE_INSPECT = "speech.reference.inspect.v1"
    QUALITY_VERIFY = "speech.quality.verify.v1"
    SPEAKER_VERIFY = "speech.speaker.verify.v1"
    VAD = "speech.vad.v1"
    NORMALIZE_AUDIO = "speech.normalize_audio.v1"


class VoiceSourceKind(str, Enum):
    READY = "ready"
    CLONE = "clone"
    DESIGNED = "designed"
    SAVED = "saved"


class Priority(str, Enum):
    AUTO = "auto"
    CONSISTENCY = "consistency_first"
    BEST = "best_quality"
    FAST = "fast"
    LIGHTWEIGHT = "lightweight"
    MANUAL = "manual"


class JobState(str, Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    DOWNLOADING = "downloading"
    LOADING = "loading"
    PROCESSING = "processing"
    VALIDATING = "validating"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _clean(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_clean(item) for item in value]
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    return value


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    mime_type: str
    uri: str
    size_bytes: int | None = None
    sha256: str | None = None
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


@dataclass(frozen=True, slots=True)
class SpeechEvent:
    kind: str
    value: str | float | int | None = None
    position: int | None = None
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class VoiceSource:
    kind: VoiceSourceKind
    voice_id: str | None = None
    reference: ArtifactRef | None = None
    description: str | None = None
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class SpeechSynthesisRequest:
    text: str
    voice_profile_id: str
    language: str = "auto"
    style: str = "auto"
    priority: Priority = Priority.AUTO
    voice_revision: int | None = None
    engine_override: str | None = None
    events: tuple[SpeechEvent, ...] = ()
    pronunciation_hints: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


@dataclass(frozen=True, slots=True)
class WordTiming:
    text: str
    start: float
    end: float
    confidence: float | None = None
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class SegmentTiming:
    text: str
    start: float
    end: float
    words: tuple[WordTiming, ...] = ()
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class QualityReport:
    status: str = "unknown"
    intelligibility: float | None = None
    speaker_similarity: float | None = None
    clipping_detected: bool | None = None
    warnings: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class Provenance:
    engine_id: str
    engine_version: str | None = None
    model_id: str | None = None
    model_revision: str | None = None
    recipe_revision: str | None = None
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class SpeechArtifact:
    audio: ArtifactRef
    duration_seconds: float
    language: str
    voice_profile_id: str
    voice_revision: int
    style: str
    provenance: Provenance
    segments: tuple[SegmentTiming, ...] = ()
    quality: QualityReport = field(default_factory=QualityReport)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    audio: ArtifactRef
    language: str = "auto"
    priority: Priority = Priority.AUTO
    engine_override: str | None = None
    word_timestamps: bool = True
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class TranscriptArtifact:
    text: str
    language: str | None
    segments: tuple[SegmentTiming, ...]
    provenance: Provenance
    language_probability: float | None = None
    source_audio: ArtifactRef | None = None
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


@dataclass(frozen=True, slots=True)
class JobProgress:
    state: JobState
    stage: str
    message: str = ""
    current: int | None = None
    total: int | None = None
    can_cancel: bool = True
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class EngineBinding:
    engine_id: str
    model_id: str | None = None
    recipe_id: str | None = None
    recipe_revision: str | None = None
    preferred_languages: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class VoiceProfile:
    profile_id: str
    display_name: str
    source: VoiceSource
    revision: int = 1
    default_style: str = "auto"
    consistency_locked: bool = False
    engine_bindings: tuple[EngineBinding, ...] = ()
    supported_languages: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))
