from __future__ import annotations

import re
from pathlib import Path


def sequence_pattern_from_selection(paths: list[Path]) -> Path | None:
    if not paths:
        return None

    first = paths[0]
    match = split_sequence_name(first.name)
    if match is None:
        return None

    prefix, frame_text, suffix = match
    width = len(frame_text)
    if not first.parent.exists():
        return None

    candidates = []
    for path in first.parent.iterdir():
        candidate_match = split_sequence_name(path.name)
        if candidate_match is None:
            continue
        candidate_prefix, candidate_frame, candidate_suffix = candidate_match
        if (
            candidate_prefix == prefix
            and candidate_suffix == suffix
            and len(candidate_frame) == width
        ):
            candidates.append(path)

    if not candidates:
        return None
    return first.with_name(f"{prefix}%0{width}d{suffix}")


def sequence_frames(path: Path) -> list[Path]:
    pattern = sequence_pattern_to_regex(path.name)
    if pattern is None or not path.parent.exists():
        return [path] if path.exists() else []
    return sorted(candidate for candidate in path.parent.iterdir() if pattern.match(candidate.name))


def sequence_groups(directory: Path) -> list[tuple[str, Path, Path, int, int, int, str, str]]:
    groups: dict[tuple[str, str, int], list[Path]] = {}
    if not directory.exists() or not directory.is_dir():
        return []

    for path in directory.iterdir():
        if not path.is_file():
            continue
        match = split_sequence_name(path.name)
        if match is None:
            continue
        prefix, frame_text, suffix = match
        groups.setdefault((prefix, suffix, len(frame_text)), []).append(path)

    result = []
    for (prefix, suffix, width), paths in groups.items():
        paths = sorted(paths)
        if len(paths) < 2:
            continue
        for contiguous_paths in _contiguous_ranges(paths):
            if len(contiguous_paths) < 2:
                continue
            first = contiguous_paths[0]
            last = contiguous_paths[-1]
            first_frame = split_sequence_name(first.name)[1]  # type: ignore[index]
            last_frame = split_sequence_name(last.name)[1]  # type: ignore[index]
            pattern = first.with_name(f"{prefix}%0{width}d{suffix}")
            result.append((
                f"{pattern.name}  ({first_frame}-{last_frame}, {len(contiguous_paths)} frames)",
                pattern,
                first,
                len(contiguous_paths),
                int(first_frame),
                int(last_frame),
                first_frame,
                last_frame,
            ))
    return sorted(result, key=lambda item: item[0].lower())


def _contiguous_ranges(paths: list[Path]) -> list[list[Path]]:
    ranges: list[list[Path]] = []
    current: list[Path] = []
    previous_frame: int | None = None

    for path in sorted(paths, key=_sequence_frame_number):
        match = split_sequence_name(path.name)
        if match is None:
            continue
        frame = int(match[1])
        if previous_frame is None or frame == previous_frame + 1:
            current.append(path)
        else:
            if current:
                ranges.append(current)
            current = [path]
        previous_frame = frame

    if current:
        ranges.append(current)
    return ranges


def _sequence_frame_number(path: Path) -> int:
    match = split_sequence_name(path.name)
    return int(match[1]) if match else -1


def split_sequence_name(name: str) -> tuple[str, str, str] | None:
    match = re.match(r"^(.*?)(\d+)(\.[^.]+)$", name)
    if not match:
        return None
    return match.group(1), match.group(2), match.group(3)


def sequence_pattern_has_frames(path: Path) -> bool:
    return first_sequence_frame(path) is not None


def first_sequence_frame(path: Path) -> Path | None:
    pattern = sequence_pattern_to_regex(path.name)
    if pattern is None or not path.parent.exists():
        return None
    matches = sorted(candidate for candidate in path.parent.iterdir() if pattern.match(candidate.name))
    return matches[0] if matches else None


def sequence_start_number(path: Path) -> int | None:
    first = first_sequence_frame(path)
    if first is None:
        return None
    match = split_sequence_name(first.name)
    if match is None:
        return None
    return int(match[1])


def sequence_pattern_to_regex(name: str) -> re.Pattern[str] | None:
    match = re.search(r"%0?(\d*)d", name)
    if not match:
        return None
    width_text = match.group(1)
    frame_pattern = r"\d{" + width_text + r"}" if width_text else r"\d+"
    regex = re.escape(name[: match.start()]) + frame_pattern + re.escape(name[match.end() :])
    return re.compile(f"^{regex}$")
