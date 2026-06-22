from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from .command_builder import ConvertJob, build_ffmpeg_args
from .ffprobe import duration_seconds, probe
from .sequence import sequence_pattern_has_frames


def validate_job(job: ConvertJob) -> None:
    if not job.input.exists() and not sequence_pattern_has_frames(job.input):
        raise FileNotFoundError(f"Input does not exist: {job.input}")
    if job.output.exists() and not job.overwrite:
        raise FileExistsError(f"Output already exists: {job.output}")
    if job.preset.output.get("requires_pattern") and "%" not in job.output.name:
        raise ValueError("Image sequence output must contain a frame pattern such as %04d")
    job.output.parent.mkdir(parents=True, exist_ok=True)


def run_convert(job: ConvertJob, log_path: Path | None = None) -> int:
    validate_job(job)
    probe_json = probe(job.input)
    duration = duration_seconds(probe_json)

    args = build_ffmpeg_args(job)
    progress_args = args[:-1] + ["-progress", "pipe:1", "-nostats", args[-1]]

    log_file = None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("w", encoding="utf-8")
        log_file.write("Command:\n")
        log_file.write(format_command(progress_args))
        log_file.write("\n\nOutput:\n")

    started = time.monotonic()
    process = subprocess.Popen(
        progress_args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    last_time = 0.0
    assert process.stdout is not None
    try:
        for line in process.stdout:
            if log_file:
                log_file.write(line)

            key, _, value = line.strip().partition("=")
            if key in {"out_time_ms", "out_time_us"}:
                try:
                    last_time = int(value) / 1_000_000
                except ValueError:
                    continue
                _print_progress(last_time, duration, started)
            elif key == "progress" and value == "end":
                _print_progress(duration or last_time, duration, started)
    finally:
        if log_file:
            log_file.close()

    print()
    return process.wait()


def _print_progress(current: float, duration: float | None, started: float) -> None:
    elapsed = max(time.monotonic() - started, 0.001)
    if duration and duration > 0:
        percent = min(current / duration, 1.0) * 100
        remaining = max(duration - current, 0)
        speed = current / elapsed if elapsed else 0
        eta = remaining / speed if speed > 0 else 0
        message = f"\rProgress: {percent:6.2f}% | time {current:8.2f}s | ETA {eta:6.1f}s"
    else:
        message = f"\rProgress: time {current:8.2f}s"
    sys.stdout.write(message)
    sys.stdout.flush()


def format_command(args: list[str]) -> str:
    return " ".join(_quote_arg(arg) for arg in args)


def _quote_arg(arg: str) -> str:
    if not arg or any(char.isspace() for char in arg):
        return "'" + arg.replace("'", "'\\''") + "'"
    return arg
