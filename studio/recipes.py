from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SoundRecipe:
    id: str
    name: str
    created_at: float
    voice: str | None
    model_id: str
    language: str
    style: str
    speech_speed: float
    seed: int
    generation: dict[str, Any]
    finishing: dict[str, Any]


class RecipeStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return payload if isinstance(payload, list) else []

    def list(self) -> list[SoundRecipe]:
        recipes: list[SoundRecipe] = []
        for item in self._read():
            if not isinstance(item, dict):
                continue
            try:
                recipes.append(SoundRecipe(**item))
            except TypeError:
                continue
        return sorted(recipes, key=lambda item: item.created_at, reverse=True)

    def get(self, recipe_id: str | None) -> SoundRecipe | None:
        if not recipe_id:
            return None
        return next((item for item in self.list() if item.id == recipe_id), None)

    def save(
        self,
        *,
        name: str,
        voice: str | None,
        model_id: str,
        language: str,
        style: str,
        speech_speed: float,
        seed: int,
        generation: dict[str, Any],
        finishing: dict[str, Any],
    ) -> SoundRecipe:
        label = (name or "Favorite sound").strip() or "Favorite sound"
        base = re.sub(r"[^\w\-]+", "-", label.lower(), flags=re.UNICODE).strip("-") or "sound"
        recipe_id = f"{base}-{int(time.time() * 1000)}"
        recipe = SoundRecipe(
            id=recipe_id,
            name=label,
            created_at=time.time(),
            voice=voice,
            model_id=model_id,
            language=language,
            style=style,
            speech_speed=float(speech_speed),
            seed=int(seed),
            generation=dict(generation),
            finishing=dict(finishing),
        )
        rows = [item.__dict__ for item in self.list()]
        rows.insert(0, recipe.__dict__)
        self._write(rows)
        return recipe

    def delete(self, recipe_id: str | None) -> bool:
        if not recipe_id:
            return False
        rows = [item.__dict__ for item in self.list() if item.id != recipe_id]
        before = len(self.list())
        if len(rows) == before:
            return False
        self._write(rows)
        return True

    def _write(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
