from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .command_builder import ConvertJob
from .sequence import sequence_frames


EXR_EXTENSIONS = {".exr"}

OUTPUT_STRUCTURE_KEYS = {
    "channels",
    "compression",
    "dataWindow",
    "displayWindow",
    "lineOrder",
    "pixelAspectRatio",
    "screenWindowCenter",
    "screenWindowWidth",
    "writer",
}

UNWRITABLE_OPENEXR_KEYS = {
    "blackGamma",
    "converterFocalLenMultiplier",
    "converterLightLossFactor",
    "knee",
}


@dataclass(frozen=True)
class ExrMetadataCopyResult:
    enabled: bool
    copied_frames: int = 0
    skipped_frames: int = 0
    message: str = ""


def preserve_exr_metadata_for_job(job: ConvertJob) -> ExrMetadataCopyResult:
    pairs = exr_metadata_frame_pairs(job)
    if not pairs:
        return ExrMetadataCopyResult(enabled=False, message="EXR metadata copy not needed")

    copied = 0
    skipped = 0
    for source, output in pairs:
        if not source.exists() or not output.exists():
            skipped += 1
            continue
        copy_exr_header_metadata(
            source,
            output,
            preserve_pixel_aspect=bool(job.preset.filters.get("preserve_pixel_aspect")),
        )
        copied += 1

    return ExrMetadataCopyResult(
        enabled=True,
        copied_frames=copied,
        skipped_frames=skipped,
        message=f"Copied EXR metadata for {copied} frame(s)",
    )


def exr_metadata_frame_pairs(job: ConvertJob) -> list[tuple[Path, Path]]:
    if not _is_exr_path(job.input) or not _is_exr_path(job.output):
        return []

    input_is_sequence = "%" in job.input.name
    output_is_sequence = "%" in job.output.name
    if input_is_sequence != output_is_sequence:
        return []

    if input_is_sequence and output_is_sequence:
        source_start_number = job.output_start_number if job.output_start_number is not None else job.input_start_number
        source_frames = _frames_from_start(job.input, source_start_number)
        output_frames = _frames_from_start(job.output, job.output_start_number)
        return list(zip(source_frames, output_frames))

    return [(job.input, job.output)]


def copy_exr_header_metadata(source: Path, output: Path, preserve_pixel_aspect: bool = False) -> None:
    try:
        import OpenEXR  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 - user-facing optional dependency error.
        raise RuntimeError("OpenEXR Python package is required to preserve EXR metadata") from exc

    source_file = OpenEXR.InputFile(str(source))
    output_file = OpenEXR.InputFile(str(output))
    try:
        source_header = source_file.header()
        output_header = output_file.header()
        channel_names = list(output_header["channels"].keys())
        channel_data = output_file.channels(channel_names)

        merged_header = output_header.copy()
        for key, value in source_header.items():
            if (
                key in OUTPUT_STRUCTURE_KEYS
                and not (preserve_pixel_aspect and key == "pixelAspectRatio")
            ) or key in UNWRITABLE_OPENEXR_KEYS:
                continue
            merged_header[key] = value

        temp_output = output.with_name(f".{output.name}.metadata.tmp")
        writer = OpenEXR.OutputFile(str(temp_output), merged_header)
        try:
            writer.writePixels(dict(zip(channel_names, channel_data)))
        finally:
            _close_openexr_file(writer)
        os.replace(temp_output, output)
    finally:
        _close_openexr_file(source_file)
        _close_openexr_file(output_file)


def _frames_from_start(pattern: Path, start_number: int | None) -> list[Path]:
    frames = sequence_frames(pattern)
    if start_number is None:
        return frames
    return [path for path in frames if _frame_number(path) >= start_number]


def _frame_number(path: Path) -> int:
    import re

    match = re.search(r"(\d+)(?=\.[^.]+$)", path.name)
    return int(match.group(1)) if match else -1


def _is_exr_path(path: Path) -> bool:
    return path.suffix.lower() in EXR_EXTENSIONS


def _close_openexr_file(file_object: object) -> None:
    close = getattr(file_object, "close", None)
    if callable(close):
        close()
