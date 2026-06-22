from __future__ import annotations

import argparse
import json
from pathlib import Path

from .command_builder import ConvertJob, build_ffmpeg_args
from .converter import format_command, run_convert
from .ffprobe import probe
from .presets import get_preset, load_presets
from .sequence import sequence_start_number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="7th-convert")
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe_parser = subparsers.add_parser("probe", help="Show ffprobe JSON for an input file.")
    probe_parser.add_argument("input", type=Path)

    subparsers.add_parser("presets", help="List available presets.")

    build_parser = subparsers.add_parser("build", help="Print the ffmpeg command for a job.")
    _add_job_args(build_parser)

    convert_parser = subparsers.add_parser("convert", help="Run a conversion job.")
    _add_job_args(convert_parser)
    convert_parser.add_argument("--log", type=Path, help="Write command output to a log file.")

    args = parser.parse_args(argv)

    try:
        if args.command == "probe":
            print(json.dumps(probe(args.input), indent=2, ensure_ascii=False))
            return 0

        if args.command == "presets":
            for preset in load_presets().values():
                print(f"{preset.id:18} {preset.name}")
            return 0

        if args.command in {"build", "convert"}:
            job = _job_from_args(args)
            command = build_ffmpeg_args(job)
            if args.command == "build":
                print(format_command(command))
                return 0
            return run_convert(job, args.log)

    except Exception as exc:  # noqa: BLE001 - CLI should report concise user-facing errors.
        parser.exit(1, f"error: {exc}\n")

    return 0


def _add_job_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--preset", required=True, help="Preset id. Use `presets` to list ids.")
    parser.add_argument("--in", dest="in_point", help="Start time, for example 00:00:10.000")
    parser.add_argument("--out", dest="out_point", help="End time, for example 00:00:20.000")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output.")


def _job_from_args(args: argparse.Namespace) -> ConvertJob:
    preset = get_preset(args.preset)
    input_start_number = sequence_start_number(args.input)
    return ConvertJob(
        input=args.input,
        output=args.output,
        preset=preset,
        input_start_number=input_start_number,
        output_start_number=input_start_number if preset.output.get("requires_pattern") else None,
        in_point=args.in_point,
        out_point=args.out_point,
        overwrite=args.overwrite,
    )
