from __future__ import annotations

from pathlib import Path


def unsupported_ffmpeg_video_reason(probe_json: dict | None, input_path: Path | None = None) -> str | None:
    if not probe_json or not _is_mxf(probe_json, input_path):
        return None
    video_stream = _first_stream(probe_json, "video")
    if not video_stream or _video_stream_is_decodable(video_stream):
        return None
    if _looks_like_arri(probe_json):
        return (
            "ARRI MXF video is not supported by the current FFmpeg/Qt backend. "
            "Metadata can be shown, but preview and conversion are disabled for this file."
        )
    return (
        "MXF video codec is not supported by the current FFmpeg/Qt backend. "
        "Metadata can be shown, but preview and conversion are disabled for this file."
    )


def _is_mxf(probe_json: dict, input_path: Path | None) -> bool:
    if input_path and input_path.suffix.lower() == ".mxf":
        return True
    format_name = str(probe_json.get("format", {}).get("format_name") or "").lower()
    return "mxf" in format_name


def _first_stream(probe_json: dict, stream_type: str) -> dict | None:
    for stream in probe_json.get("streams", []):
        if stream.get("codec_type") == stream_type:
            return stream
    return None


def _video_stream_is_decodable(stream: dict) -> bool:
    codec = str(stream.get("codec_name") or "").lower()
    if not codec or codec == "none":
        return False
    try:
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
    except (TypeError, ValueError):
        return False
    return width > 0 and height > 0


def _looks_like_arri(probe_json: dict) -> bool:
    for tags in _tag_sources(probe_json):
        joined = " ".join(str(value) for value in tags.values()).lower()
        if "arri" in joined or "alexa" in joined or "arriraw" in joined:
            return True
    return False


def _tag_sources(probe_json: dict) -> list[dict]:
    sources: list[dict] = []
    fmt = probe_json.get("format", {})
    if isinstance(fmt.get("tags"), dict):
        sources.append(fmt["tags"])
    for stream in probe_json.get("streams", []):
        if isinstance(stream.get("tags"), dict):
            sources.append(stream["tags"])
    return sources
