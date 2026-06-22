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
    input_start_number: int | None = None
    output_start_number: int | None = None
    in_point: str | None = None
    out_point: str | None = None
    overwrite: bool = False


def build_ffmpeg_args(job: ConvertJob) -> list[str]:
    args: list[str] = ["ffmpeg", "-hide_banner"]

    if job.overwrite:
        args.append("-y")
    else:
        args.append("-n")

    if job.in_point:
        args.extend(["-ss", job.in_point])

    if job.input_start_number is not None:
        args.extend(["-start_number", str(job.input_start_number)])

    args.extend(["-i", str(job.input)])

    if job.out_point:
        args.extend(["-to", job.out_point])

    video = job.preset.video
    audio = job.preset.audio
    filters = job.preset.filters

    if video.get("enabled", True):
        _append_video_args(args, video, filters)
    else:
        args.append("-vn")

    if audio.get("enabled", True):
        _append_audio_args(args, audio)
    else:
        args.append("-an")

    if job.output_start_number is not None:
        args.extend(["-start_number", str(job.output_start_number)])

    args.append(str(job.output))
    return args


def _append_video_args(args: list[str], video: dict, filters: dict) -> None:
    codec = video.get("codec")
    if codec:
        args.extend(["-c:v", codec])

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

    vf = _video_filter(filters)
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


def _video_filter(filters: dict) -> str | None:
    parts: list[str] = []

    input_color = filters.get("input_color_space", "none")
    output_color = filters.get("output_color_space", "none")
    color_filter = _color_transfer_filter(str(input_color), str(output_color))
    if color_filter:
        parts.append(color_filter)

    scale = filters.get("scale", "keep")
    force_even = filters.get("force_even_dimensions", False)
    if isinstance(scale, dict):
        mode = scale.get("mode")
        if mode == "width":
            width = int(scale["value"])
            height_expr = "-2" if force_even else "-1"
            parts.append(f"scale={width}:{height_expr}")
        elif mode == "height":
            height = int(scale["value"])
            width_expr = "-2" if force_even else "-1"
            parts.append(f"scale={width_expr}:{height}")
    elif force_even:
        parts.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")

    fps = filters.get("fps", "source")
    if isinstance(fps, (int, float)):
        parts.append(f"fps={fps}")

    if not parts:
        return None
    return ",".join(parts)


def _color_transfer_filter(input_color: str, output_color: str) -> str | None:
    if input_color == output_color or input_color == "none" or output_color == "none":
        return None
    input_transfer = COLOR_TRANSFER_MAP.get(input_color)
    output_transfer = COLOR_TRANSFER_MAP.get(output_color)
    if not input_transfer or not output_transfer:
        return None
    return f"zscale=transferin={input_transfer}:transfer={output_transfer}"
