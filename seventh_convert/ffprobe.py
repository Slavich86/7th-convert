from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .sequence import first_sequence_frame


def probe(input_path: Path) -> dict[str, Any]:
    probe_path = input_path if input_path.exists() else first_sequence_frame(input_path)
    if probe_path is None:
        raise FileNotFoundError(f"Input does not exist: {input_path}")

    args = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        str(probe_path),
    ]
    completed = subprocess.run(args, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "ffprobe failed")
    return json.loads(completed.stdout)


def duration_seconds(probe_json: dict[str, Any]) -> float | None:
    duration = probe_json.get("format", {}).get("duration")
    if duration is None:
        return None
    try:
        return float(duration)
    except (TypeError, ValueError):
        return None
