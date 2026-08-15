import sys
import types
from pathlib import Path

from studio.models import (
    GenerationOptions,
    MultilingualV3Adapter,
    NanoAdapter,
    PARALINGUISTIC_TAGS,
    TurboAdapter,
)


class FakeModel:
    sr = 24000

    def __init__(self):
        self.prepare_calls = []
        self.generate_calls = []

    def prepare_conditionals(self, path, exaggeration=0.5):
        self.prepare_calls.append((path, exaggeration))

    def generate(self, text, **kwargs):
        self.generate_calls.append((text, kwargs))
        return object()


def _install_fake_chatterbox(monkeypatch):
    package = types.ModuleType("chatterbox")
    package.__path__ = []
    mtl_module = types.ModuleType("chatterbox.mtl_tts")
    turbo_module = types.ModuleType("chatterbox.tts_turbo")

    class FakeMultilingualTTS:
        loads = []

        @classmethod
        def from_pretrained(cls, **kwargs):
            model = FakeModel()
            cls.loads.append((kwargs, model))
            return model

    class FakeTurboTTS:
        loads = []

        @classmethod
        def from_pretrained(cls, **kwargs):
            model = FakeModel()
            cls.loads.append((kwargs, model))
            return model

    mtl_module.ChatterboxMultilingualTTS = FakeMultilingualTTS
    turbo_module.ChatterboxTurboTTS = FakeTurboTTS
    monkeypatch.setitem(sys.modules, "chatterbox", package)
    monkeypatch.setitem(sys.modules, "chatterbox.mtl_tts", mtl_module)
    monkeypatch.setitem(sys.modules, "chatterbox.tts_turbo", turbo_module)
    return FakeMultilingualTTS, FakeTurboTTS


def test_v3_adapter_uses_v3_and_caches_voice_conditionals(monkeypatch, tmp_path):
    fake_v3, _ = _install_fake_chatterbox(monkeypatch)
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"fake")
    adapter = MultilingualV3Adapter("cpu")
    options = GenerationOptions(language_id="ar", exaggeration=0.7, cfg_weight=0.3)

    adapter.generate("مرحبا", voice, options)
    adapter.generate("مرة أخرى", voice, options)

    load_kwargs, model = fake_v3.loads[-1]
    assert load_kwargs == {"device": "cpu", "t3_model": "v3"}
    assert model.prepare_calls == [(str(voice.resolve()), 0.7)]
    assert model.generate_calls[0][1]["language_id"] == "ar"
    assert model.generate_calls[0][1]["audio_prompt_path"] is None
    assert model.generate_calls[0][1]["cfg_weight"] == 0.3

    adapter.generate("تغيير التعبير", voice, GenerationOptions(language_id="ar", exaggeration=0.8))
    assert model.prepare_calls[-1] == (str(voice.resolve()), 0.8)
    assert len(model.prepare_calls) == 2


def test_turbo_and_nano_route_correct_upstream_variant(monkeypatch, tmp_path):
    _, fake_turbo = _install_fake_chatterbox(monkeypatch)
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"fake")
    options = GenerationOptions(temperature=0.65, repetition_penalty=1.3, top_p=0.9, top_k=700)

    for adapter_type, expected_nano in ((TurboAdapter, False), (NanoAdapter, True)):
        adapter = adapter_type("cpu")
        adapter.generate("Hello [laugh]", voice, options)
        load_kwargs, model = fake_turbo.loads[-1]
        assert load_kwargs == {"device": "cpu", "nano": expected_nano}
        assert model.prepare_calls == [(str(voice.resolve()), 0.0)]
        generated_text, kwargs = model.generate_calls[-1]
        assert generated_text == "Hello [laugh]"
        assert kwargs["audio_prompt_path"] is None
        assert kwargs["temperature"] == 0.65
        assert kwargs["repetition_penalty"] == 1.3
        assert kwargs["top_p"] == 0.9
        assert kwargs["top_k"] == 700


def test_expression_tags_match_upstream_turbo_demo():
    assert PARALINGUISTIC_TAGS == (
        "[clear throat]",
        "[sigh]",
        "[shush]",
        "[cough]",
        "[groan]",
        "[sniff]",
        "[gasp]",
        "[chuckle]",
        "[laugh]",
    )
