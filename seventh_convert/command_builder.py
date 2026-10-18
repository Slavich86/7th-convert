from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .presets import Preset


COLOR_TRANSFER_MAP = {
    "linear": "linear",
    "srgb": "iec61966-2-1",
    "rec709": "bt709",
}


@dataclass(frozen=True)
class ConvertJob:
    input: Path
    output: Path
    preset: Preset
    audio_input: Path | None = None
    input_start_number: int | None = None
    output_start_number: int | None = None
    in_point: str | None = None
    out_point: str | None = None
    frame_step: int | None = None
    source_frame_start: int = 0
    source_frame_end: int | None = None
    overwrite: bool = False


def build_ffmpeg_args(job: ConvertJob) -> list[str]:
    args: list[str] = ["ffmpeg", "-hide_banner"]
    sampled_sequence = job.frame_step is not None

    if job.overwrite:
        args.append("-y")
    else:
        args.append("-n")

    if job.in_point and not sampled_sequence:
        args.extend(["-ss", job.in_point])

    if job.input_start_number is not None:
        args.extend(["-start_number", str(job.input_start_number)])

    args.extend(["-i", str(job.input)])
    if job.audio_input is not None:
        args.extend(["-i", str(job.audio_input)])

    if job.out_point and not sampled_sequence:
        args.extend(["-to", job.out_point])

    video = job.preset.video
    audio = job.preset.audio
    filters = job.preset.filters
    if job.audio_input is not None:
        if video.get("enabled", True):
            args.extend(["-map", "0:v:0"])
        if audio.get("enabled", True):
            args.extend(["-map", "1:a:0"])

    if video.get("enabled", True):
        _append_video_args(args, video, filters, job)
    else:
        args.append("-vn")

    if audio.get("enabled", True):
        _append_audio_args(args, audio)
        if audio.get("shortest"):
            args.append("-shortest")
    else:
        args.append("-an")

    if sampled_sequence:
        args.extend(["-fps_mode", "passthrough", "-frame_pts", "1"])
    elif job.output_start_number is not None:
        args.extend(["-start_number", str(job.output_start_number)])

    args.append(str(job.output))
    return args


def build_preview_frame_args(input_path: Path, output_path: Path | None, preset: Preset, position_ms: int | None = None) -> list[str]:
    """Render one preview frame through the same filters used by export."""
    args = ["ffmpeg", "-hide_banner", "-y"]
    if position_ms is not None:
        args.extend(["-ss", _milliseconds_to_ffmpeg_time(max(position_ms, 0))])
    args.extend(["-i", str(input_path)])
    vf = _video_filter(preset.filters, preset.video)
    if vf:
        args.extend(["-vf", vf])
    args.extend(["-frames:v", "1", "-c:v", "png", "-pix_fmt", "rgb24", "-an"])
    if output_path is None:
        args.extend(["-f", "image2pipe", "pipe:1"])
    else:
        args.append(str(output_path))
    return args


def build_preview_proxy_args(input_path: Path, output_path: Path, preset: Preset, max_width: int) -> list[str]:
    """Render a playback proxy through the same color filters used by export."""
    args = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-sn",
        "-dn",
    ]
    vf = _video_filter(preset.filters, preset.video)
    scale = f"scale='min({max_width},iw)':-2:force_original_aspect_ratio=decrease"
    args.extend(["-vf", f"{vf},{scale}" if vf else scale])
    for key in ("color_primaries", "color_trc", "colorspace", "color_range"):
        value = preset.video.get(key)
        if value:
            args.extend([f"-{key}", str(value)])
    args.extend([
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(output_path),
    ])
    return args


def _append_video_args(args: list[str], video: dict, filters: dict, job: ConvertJob) -> None:
    codec = video.get("codec")
    if codec:
        args.extend(["-c:v", codec])
        if codec == "copy":
            return

    encoder_options = video.get("encoder_options", {})
    if isinstance(encoder_options, dict):
        for key, value in encoder_options.items():
            if value is None:
                continue
            args.extend([f"-{key}", str(value)])

    profile = video.get("profile")
    if profile:
        args.extend(["-profile:v", profile])

    pix_fmt = video.get("pix_fmt")
    if pix_fmt:
        args.extend(["-pix_fmt", pix_fmt])

    pixel_format = video.get("format")
    if pixel_format:
        args.extend(["-format", str(pixel_format)])

    compression = video.get("compression")
    if compression:
        args.extend(["-compression", str(compression)])

    crf = video.get("crf")
    if crf is not None:
        args.extend(["-crf", str(crf)])

    bitrate = video.get("bitrate")
    if bitrate:
        args.extend(["-b:v", str(bitrate)])

    quality = video.get("quality")
    if quality is not None:
        args.extend(["-q:v", str(quality)])

    for key in ("color_primaries", "color_trc", "colorspace", "color_range"):
        value = video.get(key)
        if value:
            args.extend([f"-{key}", str(value)])

    if video.get("palette"):
        _append_palette_filter_args(args, filters, video, job)
        return

    vf = _video_filter(filters, video, job)
    if vf:
        args.extend(["-vf", vf])


def _append_audio_args(args: list[str], audio: dict) -> None:
    codec = audio.get("codec")
    if codec:
        args.extend(["-c:a", codec])

    bitrate = audio.get("bitrate")
    if bitrate:
        args.extend(["-b:a", str(bitrate)])

    sample_rate = audio.get("sample_rate")
    if sample_rate:
        args.extend(["-ar", str(sample_rate)])

    channels = audio.get("channels")
    if isinstance(channels, int):
        args.extend(["-ac", str(channels)])


def _video_filter(filters: dict, video: dict | None = None, job: ConvertJob | None = None) -> str | None:
    parts: list[str] = []

    if job and job.frame_step is not None:
        parts.append(_source_frame_selection_filter(job))

    lut3d = filters.get("lut3d")
    if lut3d:
        parts.append(f"lut3d=file='{_escape_filter_path(str(lut3d))}':interp=tetrahedral")

    input_color = filters.get("input_color_space", "none")
    output_color = filters.get("output_color_space", "none")
    color_filter = _color_transfer_filter(str(input_color), str(output_color))
    if color_filter:
        parts.append(color_filter)

    metadata_filter = _color_metadata_filter(video or {})
    if metadata_filter:
        parts.append(metadata_filter)

    pixel_aspect = _positive_float(filters.get("output_pixel_aspect"))
    anamorph_mode = filters.get("anamorph_output", "preserve")
    baked_pixel_aspect = False
    if pixel_aspect and pixel_aspect != 1.0:
        if anamorph_mode == "bake":
            width_expr = f"trunc(iw*{pixel_aspect}/2)*2" if filters.get("force_even_dimensions", False) else f"round(iw*{pixel_aspect})"
            parts.append(f"scale={width_expr}:ih")
            parts.append("setsar=1")
            baked_pixel_aspect = True
        elif anamorph_mode == "preserve":
            parts.append(f"setsar={pixel_aspect}")

    scale = filters.get("scale", "keep")
    force_even = filters.get("force_even_dimensions", False)
    if isinstance(scale, dict):
        mode = scale.get("mode")
        if mode == "dimensions":
            width = int(scale["width"])
            height = int(scale["height"])
            parts.append(f"scale={width}:{height}")
        elif mode == "width":
            width = int(scale["value"])
            height_expr = "-2" if force_even else "-1"
            parts.append(f"scale={width}:{height_expr}")
        elif mode == "height":
            height = int(scale["value"])
            width_expr = "-2" if force_even else "-1"
            parts.append(f"scale={width_expr}:{height}")
    elif force_even and not baked_pixel_aspect:
        parts.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")

    fps = filters.get("fps", "source")
    if isinstance(fps, (int, float)):
        parts.append(f"fps={fps}")

    if not parts:
        return None
    return ",".join(parts)


def _append_palette_filter_args(args: list[str], filters: dict, video: dict, job: ConvertJob) -> None:
    vf = _video_filter(filters, video, job)
    source = f"[0:v]{vf}," if vf else "[0:v]"
    palettegen = video.get("palettegen", {}) if isinstance(video.get("palettegen"), dict) else {}
    paletteuse = video.get("paletteuse", {}) if isinstance(video.get("paletteuse"), dict) else {}
    palettegen_filter = _filter_with_options("palettegen", palettegen)
    paletteuse_filter = _filter_with_options("paletteuse", paletteuse)
    filter_complex = f"{source}split[a][b];[a]{palettegen_filter}[p];[b][p]{paletteuse_filter}[v]"
    args.extend(["-filter_complex", filter_complex, "-map", "[v]"])


def _filter_with_options(name: str, options: dict) -> str:
    option_parts = [f"{key}={value}" for key, value in options.items() if value is not None]
    if not option_parts:
        return name
    return f"{name}={':'.join(option_parts)}"


def _color_transfer_filter(input_color: str, output_color: str) -> str | None:
    if input_color == output_color or input_color == "none" or output_color == "none":
        return None
    input_transfer = COLOR_TRANSFER_MAP.get(input_color)
    output_transfer = COLOR_TRANSFER_MAP.get(output_color)
    if not input_transfer or not output_transfer:
        return None
    return f"zscale=transferin={input_transfer}:transfer={output_transfer}"


def _color_metadata_filter(video: dict) -> str | None:
    options = []
    key_map = {
        "color_primaries": "color_primaries",
        "color_trc": "color_trc",
        "colorspace": "colorspace",
        "color_range": "range",
    }
    for source_key, filter_key in key_map.items():
        value = video.get(source_key)
        if value:
            options.append(f"{filter_key}={value}")
    if not options:
        return None
    return f"setparams={':'.join(options)}"


def _source_frame_selection_filter(job: ConvertJob) -> str:
    step = job.frame_step
    if not isinstance(step, int) or step < 1:
        raise ValueError("Frame step must be a positive integer")
    if not job.preset.output.get("requires_pattern"):
        raise ValueError("Frame step is available only for image sequence output")
    if job.source_frame_start < 0:
        raise ValueError("Source frame start must not be negative")
    if job.source_frame_end is not None and job.source_frame_end <= job.source_frame_start:
        raise ValueError("Source frame end must be greater than source frame start")

    range_parts = [f"gte(n\\,{job.source_frame_start})"]
    if job.source_frame_end is not None:
        range_parts.append(f"lt(n\\,{job.source_frame_end})")
    range_parts.append(f"(eq(n\\,{job.source_frame_start})+not(mod(n+1\\,{step})))")

    # PTS becomes the 1-based source frame number for image2's -frame_pts names.
    return f"setpts=N+1,select='{ '*'.join(range_parts) }'"


def _milliseconds_to_ffmpeg_time(position_ms: int) -> str:
    total = max(position_ms, 0) / 1000
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    seconds = total % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


def _escape_filter_path(path: str) -> str:
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _positive_float(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None
