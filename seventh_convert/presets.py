from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PRESET_DIR = Path(__file__).resolve().parent.parent / "presets"


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    group: str
    output: dict[str, Any]
    video: dict[str, Any]
    audio: dict[str, Any]
    filters: dict[str, Any]


def load_presets(preset_dir: Path = PRESET_DIR) -> dict[str, Preset]:
    presets: dict[str, Preset] = {}
    for path in sorted(preset_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        preset = Preset(
            id=data["id"],
            name=data["name"],
            group=data.get("group", "Custom"),
            output=data.get("output", {}),
            video=data.get("video", {}),
            audio=data.get("audio", {}),
            filters=data.get("filters", {}),
        )
        presets[preset.id] = preset
    return presets


def get_preset(preset_id: str) -> Preset:
    presets = load_presets()
    try:
        return presets[preset_id]
    except KeyError as exc:
        available = ", ".join(sorted(presets))
        raise ValueError(f"Unknown preset '{preset_id}'. Available presets: {available}") from exc

