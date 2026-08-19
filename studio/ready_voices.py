from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from studio.protocol import EngineBinding, VoiceSourceKind
from studio.voice_profile_store import StoredVoiceProfile, VoiceProfileStore


@dataclass(frozen=True, slots=True)
class ReadyVoiceManifest:
    """Human-facing preset voice independent from UI and storage paths."""

    engine_id: str
    voice_id: str
    display_name: str
    language: str
    locale: str
    languages: tuple[str, ...] = ()
    quality_grade: str | None = None
    recommended: bool = False
    notes: str = ""


_KOKORO_US = (
    ("af_alloy", "Alloy", "C"),
    ("af_aoede", "Aoede", "C+"),
    ("af_bella", "Bella", "A-"),
    ("af_heart", "Heart", "A"),
    ("af_jessica", "Jessica", "D"),
    ("af_kore", "Kore", "C+"),
    ("af_nicole", "Nicole", "B-"),
    ("af_nova", "Nova", "C"),
    ("af_river", "River", "D"),
    ("af_sarah", "Sarah", "C+"),
    ("af_sky", "Sky", "C-"),
    ("am_adam", "Adam", "F+"),
    ("am_echo", "Echo", "D"),
    ("am_eric", "Eric", "D"),
    ("am_fenrir", "Fenrir", "C+"),
    ("am_liam", "Liam", "D"),
    ("am_michael", "Michael", "C+"),
    ("am_onyx", "Onyx", "D"),
    ("am_puck", "Puck", "C+"),
    ("am_santa", "Santa", "D-"),
)

_KOKORO_GB = (
    ("bf_alice", "Alice", "D"),
    ("bf_emma", "Emma", "B-"),
    ("bf_isabella", "Isabella", "C"),
    ("bf_lily", "Lily", "D"),
    ("bm_daniel", "Daniel", "D"),
    ("bm_fable", "Fable", "C"),
    ("bm_george", "George", "C"),
    ("bm_lewis", "Lewis", "D+"),
)

QWEN_LANGUAGES = ("zh", "en", "ja", "ko", "de", "fr", "ru", "pt", "es", "it")

# Official CustomVoice speaker set. Native language is presentation metadata only:
# upstream documents that each speaker can speak every language supported by the model.
_QWEN_READY = (
    ("Vivian", "Vivian", "zh", "zh-CN", "Bright, slightly edgy young female voice."),
    ("Serena", "Serena", "zh", "zh-CN", "Warm, gentle young female voice."),
    ("Uncle_Fu", "Uncle Fu", "zh", "zh-CN", "Seasoned male voice with a low, mellow timbre."),
    ("Dylan", "Dylan", "zh", "zh-CN", "Youthful Beijing male voice with a clear, natural timbre."),
    ("Eric", "Eric", "zh", "zh-CN", "Lively Chengdu male voice with a slightly husky brightness."),
    ("Ryan", "Ryan", "en", "en-US", "Dynamic male voice with strong rhythmic drive."),
    ("Aiden", "Aiden", "en", "en-US", "Sunny American male voice with a clear midrange."),
    ("Ono_Anna", "Ono Anna", "ja", "ja-JP", "Playful Japanese female voice with a light, nimble timbre."),
    ("Sohee", "Sohee", "ko", "ko-KR", "Warm Korean female voice with rich emotion."),
)


READY_VOICES: tuple[ReadyVoiceManifest, ...] = tuple(
    ReadyVoiceManifest(
        engine_id="kokoro",
        voice_id=voice_id,
        display_name=display_name,
        language="en",
        locale="en-US",
        quality_grade=grade,
        recommended=voice_id in {"af_heart", "af_bella", "af_nicole"},
        notes="Upstream Kokoro v1.0 ready voice.",
    )
    for voice_id, display_name, grade in _KOKORO_US
) + tuple(
    ReadyVoiceManifest(
        engine_id="kokoro",
        voice_id=voice_id,
        display_name=display_name,
        language="en",
        locale="en-GB",
        quality_grade=grade,
        recommended=voice_id == "bf_emma",
        notes="Upstream Kokoro v1.0 ready voice.",
    )
    for voice_id, display_name, grade in _KOKORO_GB
) + tuple(
    ReadyVoiceManifest(
        engine_id="qwen3-ready",
        voice_id=voice_id,
        display_name=display_name,
        language=native_language,
        locale=locale,
        languages=QWEN_LANGUAGES,
        recommended=voice_id in {"Ryan", "Aiden"},
        notes=f"Official Qwen3-TTS CustomVoice preset. Native voice: {description}",
    )
    for voice_id, display_name, native_language, locale, description in _QWEN_READY
)


def list_ready_voices(
    *,
    engine_id: str | None = None,
    language: str | None = None,
) -> tuple[ReadyVoiceManifest, ...]:
    language_code = (language or "").lower().split("-", 1)[0]
    rows = [
        voice
        for voice in READY_VOICES
        if (engine_id is None or voice.engine_id == engine_id)
        and (
            not language_code
            or language_code == voice.language
            or language_code in voice.languages
        )
    ]
    return tuple(sorted(rows, key=lambda item: (not item.recommended, item.locale, item.display_name.lower())))


def get_ready_voice(engine_id: str, voice_id: str) -> ReadyVoiceManifest:
    for voice in READY_VOICES:
        if voice.engine_id == engine_id and voice.voice_id == voice_id:
            return voice
    raise ValueError(f"Unknown ready voice '{engine_id}:{voice_id}'.")


def create_ready_voice_profile(
    store: VoiceProfileStore,
    profile_id: str,
    *,
    engine_id: str,
    voice_id: str,
    display_name: str | None = None,
    model_id: str | None = None,
    model_revision: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> StoredVoiceProfile:
    """Create a durable VoiceProfile from a catalogue preset.

    A ready voice is engine-owned identity. Persisting only ``voice_id`` would leave a
    future router to guess between providers that happen to expose READY_VOICE. This
    helper therefore creates the semantic source and immediately promotes one explicit
    engine binding. External projects still reference only the resulting profile ID.
    """

    ready = get_ready_voice(engine_id, voice_id)
    supported_languages = ready.languages or (ready.language,)
    store.create(
        profile_id,
        display_name or ready.display_name,
        source_kind=VoiceSourceKind.READY,
        source_voice_id=ready.voice_id,
        supported_languages=supported_languages,
        metadata={
            "ready_voice_engine": ready.engine_id,
            "ready_voice_locale": ready.locale,
            "ready_voice_native_language": ready.language,
            **dict(metadata or {}),
        },
    )
    return store.add_binding(
        profile_id,
        EngineBinding(
            engine_id=ready.engine_id,
            model_id=model_id,
            model_revision=model_revision,
            engine_voice_id=ready.voice_id,
            certified_languages=supported_languages,
        ),
        promote_revision=True,
    )
