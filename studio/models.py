from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


MULTILINGUAL_LANGUAGES: dict[str, str] = {
    "ar": "Arabic",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "fi": "Finnish",
    "fr": "French",
    "he": "Hebrew",
    "hi": "Hindi",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "ms": "Malay",
    "nl": "Dutch",
    "no": "Norwegian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ru": "Russian",
    "sv": "Swedish",
    "sw": "Swahili",
    "tr": "Turkish",
    "zh": "Chinese",
}

# Keep this list aligned with the official Turbo demo at the pinned upstream
# Chatterbox revision. Nano shares the Turbo text/tokenizer architecture.
PARALINGUISTIC_TAGS: tuple[str, ...] = (
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


@dataclass(frozen=True)
class ModelCapabilities:
    multilingual: bool
    supports_exaggeration: bool
    supports_cfg: bool
    supports_min_p: bool
    supports_top_k: bool
    supports_paralinguistic_tags: bool
    cpu_friendly: bool = False


@dataclass(frozen=True)
class ModelSpec:
    id: str
    name: str
    short_name: str
    description: str
    languages: tuple[str, ...]
    capabilities: ModelCapabilities

    def language_choices(self) -> list[str]:
        if not self.capabilities.multilingual:
            return ["English"]
        return [MULTILINGUAL_LANGUAGES[code] for code in self.languages]


MODEL_SPECS: dict[str, ModelSpec] = {
    "multilingual-v3": ModelSpec(
        id="multilingual-v3",
        name="Chatterbox Multilingual V3",
        short_name="V3",
        description="Best general choice for multilingual creator voiceovers and cross-language cloning.",
        languages=tuple(MULTILINGUAL_LANGUAGES),
        capabilities=ModelCapabilities(
            multilingual=True,
            supports_exaggeration=True,
            supports_cfg=True,
            supports_min_p=True,
            supports_top_k=False,
            supports_paralinguistic_tags=False,
        ),
    ),
    "turbo": ModelSpec(
        id="turbo",
        name="Chatterbox Turbo",
        short_name="Turbo",
        description="Fast English model with native paralinguistic tags for expressive speech.",
        languages=("en",),
        capabilities=ModelCapabilities(
            multilingual=False,
            supports_exaggeration=False,
            supports_cfg=False,
            supports_min_p=False,
            supports_top_k=True,
            supports_paralinguistic_tags=True,
        ),
    ),
    "nano": ModelSpec(
        id="nano",
        name="Chatterbox Nano",
        short_name="Nano",
        description="Small English model for CPU-friendly and resource-constrained local generation.",
        languages=("en",),
        capabilities=ModelCapabilities(
            multilingual=False,
            supports_exaggeration=False,
            supports_cfg=False,
            supports_min_p=False,
            supports_top_k=True,
            supports_paralinguistic_tags=True,
            cpu_friendly=True,
        ),
    ),
}

DEFAULT_MODEL_ID = "multilingual-v3"


def model_choices() -> list[str]:
    return [spec.name for spec in MODEL_SPECS.values()]


def model_id_from_name(name: str | None) -> str:
    for model_id, spec in MODEL_SPECS.items():
        if name == spec.name:
            return model_id
    return DEFAULT_MODEL_ID


def model_name(model_id: str) -> str:
    return MODEL_SPECS.get(model_id, MODEL_SPECS[DEFAULT_MODEL_ID]).name


def language_code_from_name(name: str | None) -> str:
    if not name:
        return "en"
    for code, display in MULTILINGUAL_LANGUAGES.items():
        if display == name:
            return code
    return "en"


def language_name_from_code(code: str | None) -> str:
    return MULTILINGUAL_LANGUAGES.get(code or "en", "English")


@dataclass(frozen=True)
class GenerationOptions:
    language_id: str = "en"
    exaggeration: float = 0.5
    cfg_weight: float = 0.5
    temperature: float = 0.8
    repetition_penalty: float = 1.2
    min_p: float = 0.05
    top_p: float = 1.0
    top_k: int = 1000


class ModelAdapter(Protocol):
    spec: ModelSpec

    @property
    def sample_rate(self) -> int: ...

    @property
    def loaded(self) -> bool: ...

    def generate(self, text: str, voice_path: Path, options: GenerationOptions): ...

    def unload(self) -> None: ...


class _BaseAdapter:
    spec: ModelSpec

    def __init__(self, device: str, model_dir: str | Path | None = None):
        self.device = device
        self.model_dir = Path(model_dir) if model_dir else None
        self._model = None
        self._voice_key: tuple[str, float] | tuple[str] | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def sample_rate(self) -> int:
        model = self.load()
        return int(model.sr)

    def load(self):
        raise NotImplementedError

    def unload(self) -> None:
        self._model = None
        self._voice_key = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


class MultilingualV3Adapter(_BaseAdapter):
    spec = MODEL_SPECS["multilingual-v3"]

    def load(self):
        if self._model is None:
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS

            if self.model_dir is not None:
                self._model = ChatterboxMultilingualTTS.from_local(
                    self.model_dir,
                    self.device,
                    t3_model="v3",
                )
            else:
                self._model = ChatterboxMultilingualTTS.from_pretrained(
                    device=self.device,
                    t3_model="v3",
                )
        return self._model

    def _prepare_voice(self, voice_path: Path, exaggeration: float) -> None:
        model = self.load()
        key = (str(voice_path.resolve()), round(float(exaggeration), 4))
        if key != self._voice_key:
            model.prepare_conditionals(str(voice_path), exaggeration=float(exaggeration))
            self._voice_key = key

    def generate(self, text: str, voice_path: Path, options: GenerationOptions):
        model = self.load()
        self._prepare_voice(voice_path, options.exaggeration)
        return model.generate(
            text,
            language_id=options.language_id,
            audio_prompt_path=None,
            exaggeration=float(options.exaggeration),
            cfg_weight=float(options.cfg_weight),
            temperature=float(options.temperature),
            repetition_penalty=float(options.repetition_penalty),
            min_p=float(options.min_p),
            top_p=float(options.top_p),
        )


class TurboAdapter(_BaseAdapter):
    spec = MODEL_SPECS["turbo"]
    nano = False

    def load(self):
        if self._model is None:
            from chatterbox.tts_turbo import ChatterboxTurboTTS

            if self.model_dir is not None:
                self._model = ChatterboxTurboTTS.from_local(
                    self.model_dir,
                    self.device,
                    nano=self.nano,
                )
            else:
                self._model = ChatterboxTurboTTS.from_pretrained(
                    device=self.device,
                    nano=self.nano,
                )
        return self._model

    def _prepare_voice(self, voice_path: Path) -> None:
        model = self.load()
        key = (str(voice_path.resolve()),)
        if key != self._voice_key:
            model.prepare_conditionals(str(voice_path), exaggeration=0.0)
            self._voice_key = key

    def generate(self, text: str, voice_path: Path, options: GenerationOptions):
        model = self.load()
        self._prepare_voice(voice_path)
        return model.generate(
            text,
            audio_prompt_path=None,
            temperature=float(options.temperature),
            repetition_penalty=float(options.repetition_penalty),
            top_p=float(options.top_p),
            top_k=int(options.top_k),
        )


class NanoAdapter(TurboAdapter):
    spec = MODEL_SPECS["nano"]
    nano = True


ADAPTER_TYPES = {
    "multilingual-v3": MultilingualV3Adapter,
    "turbo": TurboAdapter,
    "nano": NanoAdapter,
}


def create_adapter(model_id: str, device: str, model_dir: str | Path | None = None) -> ModelAdapter:
    try:
        adapter_type = ADAPTER_TYPES[model_id]
    except KeyError as exc:
        raise ValueError(f"Unknown Chatterbox model: {model_id}") from exc
    return adapter_type(device, model_dir=model_dir)
