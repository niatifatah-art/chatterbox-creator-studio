from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .audio import AudioProcessOptions, process_audio
from .batch import BatchItem
from .engine import ChatterboxEngine
from .reliability import GenerationPolicy, generate_reliably


@dataclass(frozen=True)
class BatchGenerationSummary:
    output_dir: Path
    manifest_path: Path
    generated: int
    failed: int


def run_batch(
    engine: ChatterboxEngine,
    items: list[BatchItem],
    output_root: str | Path,
    generation_kwargs: dict[str, Any],
    policy: GenerationPolicy | None = None,
    fit_to_timing: bool = False,
    max_duration_stretch: float = 1.18,
) -> BatchGenerationSummary:
    if not items:
        raise ValueError("Batch is empty.")
    root = Path(output_root)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_dir = root / f"batch_{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    generated = 0
    failed = 0

    for index, item in enumerate(items, 1):
        row: dict[str, Any] = {
            "index": index,
            "id": item.id,
            "text": item.text,
            "start_seconds": item.start_seconds,
            "end_seconds": item.end_seconds,
            "target_duration_seconds": item.target_duration_seconds,
        }
        try:
            reliable = generate_reliably(
                engine,
                item.text,
                policy=policy,
                **generation_kwargs,
            )
            selected = reliable.selected.result
            safe_id = "".join(char if char.isalnum() or char in "-_" else "-" for char in item.id).strip("-") or str(index)
            destination = output_dir / f"{index:04d}_{safe_id}.wav"
            shutil.copy2(selected.audio_path, destination)
            metadata_destination = destination.with_suffix(".json")
            shutil.copy2(selected.metadata_path, metadata_destination)
            if fit_to_timing and item.target_duration_seconds and item.target_duration_seconds > 0:
                fitted = output_dir / f"{index:04d}_{safe_id}_fit.wav"
                process_audio(
                    destination,
                    fitted,
                    AudioProcessOptions(
                        target_duration_seconds=item.target_duration_seconds,
                        max_duration_stretch=float(max_duration_stretch),
                    ),
                )
                destination = fitted
            row.update(
                {
                    "status": "ok",
                    "output": str(destination),
                    "metadata": str(metadata_destination),
                    "seed": selected.seed,
                    "score": reliable.selected.score,
                    "candidate_count": len(reliable.candidates),
                }
            )
            generated += 1
        except Exception as exc:
            row.update({"status": "error", "error": str(exc)})
            failed += 1
        manifest.append(row)

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return BatchGenerationSummary(
        output_dir=output_dir,
        manifest_path=manifest_path,
        generated=generated,
        failed=failed,
    )
