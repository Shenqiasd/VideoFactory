from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List


_SRT_BLOCK_PATTERN = re.compile(
    r"(\d+)\s*\n"
    r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\n"
    r"(.*?)(?=\n\s*\n\d+\s*\n|\Z)",
    re.S,
)


def slugify(text: str, fallback: str = "segment") -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", (text or "").strip()).strip("_").lower()
    return value[:48] if value else fallback


def srt_time_to_seconds(time_str: str) -> float:
    parts = time_str.replace(",", ".").split(":")
    hours = float(parts[0])
    minutes = float(parts[1])
    seconds = float(parts[2])
    return hours * 3600 + minutes * 60 + seconds


def format_srt_time(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    secs = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def parse_srt_file(srt_path: str) -> List[Dict[str, Any]]:
    if not srt_path or not os.path.exists(srt_path):
        return []

    content = Path(srt_path).read_text(encoding="utf-8", errors="ignore").replace("\r\n", "\n")
    entries: List[Dict[str, Any]] = []
    for match in _SRT_BLOCK_PATTERN.finditer(content):
        text_lines = [line.strip() for line in match.group(4).split("\n") if line.strip()]
        entries.append(
            {
                "index": int(match.group(1)),
                "start": srt_time_to_seconds(match.group(2)),
                "end": srt_time_to_seconds(match.group(3)),
                "start_raw": match.group(2),
                "end_raw": match.group(3),
                "lines": text_lines,
                "text": " ".join(text_lines).strip(),
            }
        )
    return entries


def write_srt_entries(entries: List[Dict[str, Any]], output_path: str):
    blocks: List[str] = []
    for idx, entry in enumerate(entries, start=1):
        lines = [str(line).strip() for line in entry.get("lines", []) if str(line).strip()]
        blocks.append(
            f"{idx}\n"
            f"{format_srt_time(float(entry.get('start', 0.0)))} --> {format_srt_time(float(entry.get('end', 0.0)))}\n"
            f"{os.linesep.join(lines or [' '])}"
        )
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")


def extract_subtitle_window(entries: List[Dict[str, Any]], start: float, end: float) -> List[Dict[str, Any]]:
    window_entries: List[Dict[str, Any]] = []
    for entry in entries:
        if entry["end"] <= start or entry["start"] >= end:
            continue
        window_entries.append(
            {
                "start": max(0.0, entry["start"] - start),
                "end": max(0.0, min(end, entry["end"]) - start),
                "lines": list(entry.get("lines", [])),
                "text": entry.get("text", ""),
            }
        )
    return window_entries


def subtitle_excerpt(entries: List[Dict[str, Any]], start: float, end: float) -> str:
    parts = [entry.get("text", "") for entry in entries if entry["start"] < end and entry["end"] > start]
    return " ".join(part.strip() for part in parts if part.strip()).strip()
