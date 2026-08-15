from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class BatchItem:
    id: str
    text: str
    start_seconds: float | None = None
    end_seconds: float | None = None

    @property
    def target_duration_seconds(self) -> float | None:
        if self.start_seconds is None or self.end_seconds is None:
            return None
        return max(0.0, self.end_seconds - self.start_seconds)


def _parse_timestamp(value: str) -> float:
    """Parse SRT HH:MM:SS,mmm and WebVTT [HH:]MM:SS.mmm timestamps."""
    token = (value or "").strip().split()[0].replace(",", ".")
    parts = token.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = "0"
        minutes, seconds = parts
    else:
        raise ValueError(f"Invalid subtitle timestamp: {value!r}")
    result = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if result < 0:
        raise ValueError(f"Invalid subtitle timestamp: {value!r}")
    return result


def _parse_time_value(value) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if ":" in text:
        return _parse_timestamp(text)
    return float(text)


def parse_srt_or_vtt(text: str) -> list[BatchItem]:
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if cleaned.lstrip().startswith("WEBVTT"):
        cleaned = cleaned.lstrip()[6:].lstrip("\n")
    blocks = re.split(r"\n\s*\n", cleaned.strip()) if cleaned.strip() else []
    items: list[BatchItem] = []
    auto_id = 1
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        # Ignore WebVTT NOTE/STYLE/REGION blocks rather than mistaking their text for cues.
        if lines[0].upper().startswith(("NOTE", "STYLE", "REGION")):
            continue
        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        try:
            start_text, end_text = lines[timing_index].split("-->", 1)
            start = _parse_timestamp(start_text)
            # WebVTT may append cue settings after the end timestamp.
            end = _parse_timestamp(end_text)
        except (ValueError, TypeError):
            continue
        if end < start:
            continue
        cue_id = lines[0] if timing_index > 0 and "-->" not in lines[0] else str(auto_id)
        cue_text = " ".join(lines[timing_index + 1 :]).strip()
        if cue_text:
            items.append(BatchItem(id=str(cue_id), text=cue_text, start_seconds=start, end_seconds=end))
            auto_id += 1
    return items


def parse_txt(text: str) -> list[BatchItem]:
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = [block.strip() for block in re.split(r"\n\s*\n", cleaned) if block.strip()]
    if len(blocks) <= 1:
        blocks = [line.strip() for line in cleaned.splitlines() if line.strip()]
    return [BatchItem(id=str(index), text=block) for index, block in enumerate(blocks, 1)]


def parse_json(text: str) -> list[BatchItem]:
    payload = json.loads(text)
    if isinstance(payload, dict):
        payload = payload.get("items") or payload.get("segments") or []
    if not isinstance(payload, list):
        raise ValueError("JSON batch input must be a list or contain an 'items'/'segments' list.")
    items: list[BatchItem] = []
    for index, entry in enumerate(payload, 1):
        if isinstance(entry, str):
            items.append(BatchItem(id=str(index), text=entry))
            continue
        if not isinstance(entry, dict):
            continue
        value = str(entry.get("text") or entry.get("script") or "").strip()
        if not value:
            continue
        start = entry.get("start_seconds", entry.get("start"))
        end = entry.get("end_seconds", entry.get("end"))
        try:
            start_seconds = _parse_time_value(start)
            end_seconds = _parse_time_value(end)
        except ValueError as exc:
            raise ValueError(f"Invalid timing in JSON item {entry.get('id') or index}: {exc}") from exc
        items.append(
            BatchItem(
                id=str(entry.get("id") or index),
                text=value,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
            )
        )
    return items


def parse_csv_file(path: Path) -> list[BatchItem]:
    items: list[BatchItem] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader, 1):
            value = str(row.get("text") or row.get("script") or row.get("line") or "").strip()
            if not value:
                continue
            start = row.get("start_seconds") or row.get("start")
            end = row.get("end_seconds") or row.get("end")
            try:
                start_seconds = _parse_time_value(start)
                end_seconds = _parse_time_value(end)
            except ValueError as exc:
                raise ValueError(f"Invalid timing in CSV row {index}: {exc}") from exc
            items.append(
                BatchItem(
                    id=str(row.get("id") or index),
                    text=value,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                )
            )
    return items


def load_batch(path: str | Path) -> list[BatchItem]:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError("Batch file not found.")
    suffix = source.suffix.lower()
    if suffix == ".csv":
        items = parse_csv_file(source)
    else:
        text = source.read_text(encoding="utf-8-sig")
        if suffix in {".srt", ".vtt"}:
            items = parse_srt_or_vtt(text)
        elif suffix == ".json":
            items = parse_json(text)
        elif suffix in {".txt", ".md"}:
            items = parse_txt(text)
        else:
            raise ValueError("Supported batch files: TXT, MD, CSV, JSON, SRT and VTT.")
    if not items:
        raise ValueError("No usable batch items were found in the file.")
    return items


def preview_rows(items: list[BatchItem], limit: int = 200) -> list[list[object]]:
    return [
        [item.id, item.text, item.start_seconds, item.end_seconds, item.target_duration_seconds]
        for item in items[:limit]
    ]


def write_manifest(items: list[BatchItem], path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    for item in items:
        row = asdict(item)
        row["target_duration_seconds"] = item.target_duration_seconds
        payload.append(row)
    destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return destination
