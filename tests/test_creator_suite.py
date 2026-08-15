from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path

from studio.batch import BatchItem, parse_json, parse_srt_or_vtt, parse_txt
from studio.batch_runner import run_batch
from studio.engine import GenerationResult
from studio.preprocess import PreprocessOptions, process_text
from studio.projects import ProjectStore
from studio.quality import analyze_audio, text_similarity
from studio.reliability import GenerationPolicy, generate_reliably
from studio.voices import VoiceLibrary


def _write_wav(path: Path, seconds: float = 1.0, sample_rate: int = 8000, amplitude: int = 6000) -> None:
    frames = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        for index in range(frames):
            value = int(amplitude * math.sin(2 * math.pi * 220 * index / sample_rate))
            handle.writeframesraw(struct.pack("<h", value))


def test_preprocess_is_opt_in_and_previewable():
    source = "Hello…   world!"
    untouched = process_text(
        source,
        options=PreprocessOptions(
            normalize_unicode=False,
            normalize_punctuation=False,
            normalize_whitespace=False,
        ),
    )
    assert untouched.processed == source
    changed = process_text(
        source,
        options=PreprocessOptions(normalize_punctuation=True, normalize_whitespace=True),
    )
    assert changed.processed == "Hello... world!"
    assert changed.changed


def test_batch_parses_srt_vtt_json_and_txt():
    srt = "1\n00:00:01,000 --> 00:00:03,500\nHello there.\n\n2\n00:00:04,000 --> 00:00:05,000\nBye."
    items = parse_srt_or_vtt(srt)
    assert len(items) == 2
    assert items[0].target_duration_seconds == 2.5
    vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.250\nHi"
    assert parse_srt_or_vtt(vtt)[0].target_duration_seconds == 1.25
    assert parse_json('[{"id":"x","text":"Hello"}]')[0].id == "x"
    assert len(parse_txt("one\n\ntwo")) == 2


def test_project_store_round_trip_and_takes(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("My Video")
    saved = store.save(project["id"], {"script": "hello", "voice": "me"})
    assert saved["script"] == "hello"
    assert store.list()[0].id == project["id"]
    audio = tmp_path / "take.wav"
    _write_wav(audio, 0.25)
    metadata = tmp_path / "take.json"
    metadata.write_text('{"seed": 1}', encoding="utf-8")
    copied = store.add_take(project["id"], audio, metadata)
    assert Path(copied["audio"]).exists()
    assert len(store.list_takes(project["id"])) == 1
    store.select_take(project["id"], "full", copied["audio"])
    assert "full" in store.load(project["id"])["selected_takes"]


def test_voice_library_management_and_analysis(tmp_path):
    source = tmp_path / "source.wav"
    _write_wav(source, 1.0)
    library = VoiceLibrary(tmp_path / "voices")
    name, path = library.save(str(source), "Test Voice")
    assert name == "Test-Voice"
    profile = library.profile(name)
    assert profile is not None
    assert 0.9 < profile.duration_seconds < 1.1
    renamed = library.rename(name, "Renamed")
    assert renamed == "Renamed"
    duplicate = library.duplicate(renamed)
    assert duplicate.startswith("Renamed-copy")
    assert library.delete(duplicate)


def test_text_similarity_ignores_pause_tokens():
    assert text_similarity("Hello [pause=0.5] world!", "hello world") > 0.95


def test_quality_check_accepts_basic_voiced_audio(tmp_path):
    audio = tmp_path / "clean.wav"
    _write_wav(audio, 0.5)
    report = analyze_audio(audio)
    assert report.duration_seconds > 0.45
    assert report.score > 0.8


class _FakeEngine:
    def __init__(self, directory: Path):
        self.directory = directory
        self.counter = 0

    def generate(self, script: str, **kwargs):
        self.counter += 1
        audio = self.directory / f"{self.counter}.wav"
        metadata = self.directory / f"{self.counter}.json"
        _write_wav(audio, 0.5, amplitude=5000 + self.counter * 100)
        metadata.write_text(json.dumps({"original_script": script}), encoding="utf-8")
        return GenerationResult(
            audio_path=audio,
            metadata_path=metadata,
            model_id="nano",
            model_name="Nano",
            seed=100 + self.counter,
            chunk_count=1,
        )


def test_reliability_and_batch_work_without_optional_dependencies(tmp_path):
    engine = _FakeEngine(tmp_path)
    reliable = generate_reliably(
        engine,
        "hello",
        policy=GenerationPolicy(best_of_n=2),
        voice_path=tmp_path / "voice.wav",
        model_id="nano",
        language_id="en",
        seed=10,
    )
    assert len(reliable.candidates) == 2
    assert all(candidate.quality is not None for candidate in reliable.candidates)
    assert reliable.selected.result.audio_path.exists()
    payload = json.loads(reliable.selected.result.metadata_path.read_text(encoding="utf-8"))
    assert payload["studio_reliability"]["quality_used_for_best_of"] is True

    summary = run_batch(
        engine,
        [BatchItem(id="1", text="hello"), BatchItem(id="2", text="world")],
        tmp_path / "batch",
        generation_kwargs={"voice_path": tmp_path / "voice.wav", "model_id": "nano", "language_id": "en", "seed": -1},
        policy=GenerationPolicy(),
    )
    assert summary.generated == 2
    assert summary.failed == 0
    assert summary.manifest_path.exists()
