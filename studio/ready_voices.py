from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReadyVoiceManifest:
    """Human-facing preset voice independent from UI and storage paths."""

    engine_id: str
    voice_id: str
    display_name: str
    language: str
    locale: str
    quality_grade: str | None = None
    recommended: bool = False
    notes: str = ""


# Kokoro v1.0 English voices from the official model repository. We keep the upstream
# IDs unchanged because the voice pack filenames use them. Grades are upstream guidance,
# not our own quality claim; Phase 9 will add local measured certification.
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
        and (not language_code or voice.language == language_code)
    ]
    return tuple(sorted(rows, key=lambda item: (not item.recommended, item.locale, item.display_name.lower())))


def get_ready_voice(engine_id: str, voice_id: str) -> ReadyVoiceManifest:
    for voice in READY_VOICES:
        if voice.engine_id == engine_id and voice.voice_id == voice_id:
            return voice
    raise ValueError(f"Unknown ready voice '{engine_id}:{voice_id}'.")
