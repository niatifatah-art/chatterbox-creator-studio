from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_SETTINGS: dict[str, Any] = {
    "voice": None,
    "project": None,
    "model_id": "multilingual-v3",
    "model_selection": "Auto",
    "language": "en",
    "language_selection": "Auto",
    "preset": "Creator",
    "generation_quality": "Balanced",
    "compute_preference": "Auto",
    "offline_mode": False,
    "auto_download_models": True,
    "exaggeration": 0.65,
    "cfg_weight": 0.30,
    "temperature": 0.80,
    "repetition_penalty": 1.20,
    "min_p": 0.05,
    "top_p": 1.00,
    "top_k": 1000,
    "speech_speed": 1.00,
    "raw_mode": False,
    "smart_chunking": True,
    "max_chars": 280,
    "chunk_gap_seconds": 0.06,
    "seed": -1,
    "normalize_unicode": True,
    "normalize_punctuation": False,
    "normalize_numbers": False,
    "replace_urls": False,
    "collapse_repeated_punctuation": False,
    "normalize_whitespace": True,
    "quality_check": False,
    "verify_stt": False,
    "whisper_model": "tiny",
    "verification_threshold": 0.78,
    "auto_retries": 0,
    "best_of_n": 1,
    "trim_silence": False,
    "peak_normalize": False,
    "fade_ms": 0,
    "batch_fit_timing": False,
    "batch_max_stretch": 1.18,
}


class SettingsStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return DEFAULT_SETTINGS.copy()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return DEFAULT_SETTINGS.copy()
        merged = DEFAULT_SETTINGS.copy()
        if isinstance(payload, dict):
            for key in DEFAULT_SETTINGS:
                if key in payload:
                    merged[key] = payload[key]
        return merged

    def save(self, values: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        safe = DEFAULT_SETTINGS.copy()
        for key in DEFAULT_SETTINGS:
            if key in values:
                safe[key] = values[key]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(safe, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
