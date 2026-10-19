"""Display-only color processing; never reads or mutates export presets."""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
import tempfile

from .presets import Preset

try:
    import PyOpenColorIO as ocio
except ImportError:
    ocio = None

MONITORS = (("sRGB Monitor", "srgb"), ("Rec.709 Monitor", "rec709"))
_TARGETS = {
    "srgb": ("sRGB", "Output - sRGB", "Utility - sRGB - Texture"),
    "rec709": ("rec709", "Rec.709", "Rec709", "Output - Rec.709"),
}


def _config(path: str):
    if ocio is None:
        raise ValueError("Monitor display transform requires PyOpenColorIO")
    return ocio.Config.CreateFromFile(path)


def _target(config, monitor: str) -> str:
    if monitor not in _TARGETS:
        raise ValueError(f"Unknown player monitor: {monitor}")
    for name in _TARGETS[monitor]:
        if config.getColorSpace(name) is not None:
            return name
    raise ValueError(f"OCIO config has no {monitor} monitor color space")


def display_preset(input_transform: str, monitor: str, config_path: str, enabled: bool) -> Preset:
    filters = {}
    if enabled:
        try:
            path = Path(config_path).expanduser().resolve(strict=True)
            source = input_transform.removeprefix("ocio:")
            _, lut = _display_lut(str(path), path.stat().st_mtime_ns, source, monitor)
            if lut:
                filters["lut3d"] = lut
        except Exception as exc:
            raise ValueError(f"Player display {input_transform} → {monitor}: {exc}") from exc
    return Preset(id="player_display", name="Player display", group="Preview",
                  output={}, video={}, audio={"enabled": False}, filters=filters)


@lru_cache(maxsize=32)
def _display_lut(config_path: str, modified: int, source: str, monitor: str):
    config = _config(config_path)
    target = _target(config, monitor)
    # Validate even identity transforms; invalid source names must not become success.
    config.getProcessor(source, target)
    if source == target:
        return target, None
    stamp = config.getCacheID()
    key = hashlib.sha256(f"display-v1|{config_path}|{modified}|{stamp}|{source}|{target}|32".encode()).hexdigest()
    path = Path(tempfile.gettempdir()) / "7th-convert" / "display_luts" / f"{key}.cube"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        baker = ocio.Baker()
        baker.setConfig(config)
        baker.setFormat("iridas_cube")
        baker.setInputSpace(source)
        baker.setTargetSpace(target)
        baker.setCubeSize(32)
        # Atomic publication prevents a reader seeing an incomplete LUT.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            try:
                handle.write(baker.bake())
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        temporary.replace(path)
    return target, str(path)


@lru_cache(maxsize=32)
def display_processor(config_path: str, modified: int, source: str, monitor: str):
    """Qt fallback CPU processor using the same spaces as the Rust display LUT."""
    config = _config(config_path)
    return config.getProcessor(source.removeprefix("ocio:"), _target(config, monitor)).getDefaultCPUProcessor()
