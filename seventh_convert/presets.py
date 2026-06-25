from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any


PRESET_PACKAGE = "seventh_convert.preset_data"


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    group: str
    output: dict[str, Any]
    video: dict[str, Any]
    audio: dict[str, Any]
    filters: dict[str, Any]


def load_presets(preset_dir: Path | None = None) -> dict[str, Preset]:
    presets: dict[str, Preset] = {}
    if preset_dir is not None:
        paths = sorted(preset_dir.glob("*.json"))
        for path in paths:
            preset_id, preset = _load_preset_json(path.read_text(encoding="utf-8"))
            presets[preset_id] = preset
        return presets

    for resource in sorted(resources.files(PRESET_PACKAGE).iterdir(), key=lambda item: item.name):
        if resource.name.endswith(".json") and resource.is_file():
            preset_id, preset = _load_preset_json(resource.read_text(encoding="utf-8"))
            presets[preset_id] = preset
    return presets


def _load_preset_json(text: str) -> tuple[str, Preset]:
    data = json.loads(text)
    preset = Preset(
        id=data["id"],
        name=data["name"],
        group=data.get("group", "Custom"),
        output=data.get("output", {}),
        video=data.get("video", {}),
        audio=data.get("audio", {}),
        filters=data.get("filters", {}),
    )
    return preset.id, preset


def get_preset(preset_id: str) -> Preset:
    presets = load_presets()
    try:
        return presets[preset_id]
    except KeyError as exc:
        available = ", ".join(sorted(presets))
        raise ValueError(f"Unknown preset '{preset_id}'. Available presets: {available}") from exc
