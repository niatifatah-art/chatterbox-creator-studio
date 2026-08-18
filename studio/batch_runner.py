from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .audio import AudioProcessOptions, process_audio
from .batch import BatchItem
from .reliability import GenerationEngine, GenerationPolicy, generate_reliably


BatchProgressCallback = Callable[[str, int | None, int | None], None]


@dataclass(frozen=True)
class BatchGenerationSummary:
    output_dir: Path
    manifest_path: Path
    generated: int
    failed: int


def run_batch(
    engine: GenerationEngine,
    items: list[BatchItem],
    output_root: str | Path,
    generation_kwargs: dict[str, Any],
    policy: GenerationPolicy | None = None,
    fit_to_timing: bool = False,
    max_duration_stretch: float = 1.18,
    progress_callback: BatchProgressCallback | None = None,
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
    total_items = len(items)

    for index, item in enumerate(items, 1):
        if progress_callback:
            progress_callback(f"Generating item {index} of {total_items}…", index - 1, total_items)
        row: dict[str, Any] = {
            "index": index,
            "id": item.id,
            "text": item.text,
            "start_seconds": item.start_seconds,
            "end_seconds": item.end_seconds,
            "target_duration_seconds": item.target_duration_seconds,
        }
        try:
            kwargs = dict(generation_kwargs)
            if progress_callback:
                def engine_progress(desc: str, current: int | None, total: int | None, *, _index=index) -> None:
                    suffix = ""
                    if current is not None and total:
                        suffix = f" · chunk {current}/{total}"
                    progress_callback(f"Item {_index}/{total_items} · {desc}{suffix}", _index - 1, total_items)

                kwargs["progress_callback"] = engine_progress
            reliable = generate_reliably(
                engine,
                item.text,
                policy=policy,
                **kwargs,
            )
            selected = reliable.selected.result
            safe_id = "".join(char if char.isalnum() or char in "-_" else "-" for char in item.id).strip("-") or str(index)
            raw_destination = output_dir / f"{index:04d}_{safe_id}.wav"
            shutil.copy2(selected.audio_path, raw_destination)
            destination = raw_destination
            metadata_destination = raw_destination.with_suffix(".json")
            shutil.copy2(selected.metadata_path, metadata_destination)

            timing_warning: str | None = None
            if fit_to_timing and item.target_duration_seconds and item.target_duration_seconds > 0:
                fitted = output_dir / f"{index:04d}_{safe_id}_fit.wav"
                try:
                    process_audio(
                        raw_destination,
                        fitted,
                        AudioProcessOptions(
                            target_duration_seconds=item.target_duration_seconds,
                            max_duration_stretch=float(max_duration_stretch),
                        ),
                    )
                    destination = fitted
                except ValueError as exc:
                    timing_warning = str(exc)

            row.update(
                {
                    "status": "ok",
                    "output": str(destination),
                    "raw_output": str(raw_destination),
                    "metadata": str(metadata_destination),
                    "seed": selected.seed,
                    "score": reliable.selected.score,
                    "candidate_count": len(reliable.candidates),
                    "timing_warning": timing_warning,
                }
            )
            generated += 1
        except Exception as exc:
            row.update({"status": "error", "error": str(exc)})
            failed += 1
        manifest.append(row)
        if progress_callback:
            progress_callback(f"Finished item {index} of {total_items}", index, total_items)

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    if progress_callback:
        progress_callback("Batch complete", total_items, total_items)
    return BatchGenerationSummary(
        output_dir=output_dir,
        manifest_path=manifest_path,
        generated=generated,
        failed=failed,
    )
