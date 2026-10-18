"""Prepare a clean, reproducible converter folder from an explicit file list."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "publish"
ROOT_FILES = (
    "pyproject.toml", "requirements.txt", "README.md", "README_UA.md",
    "7th-vfx-convertor.sh", "7th-vfx-convertor.bat",
    "7th-vfx-convertor.desktop", "7th-vfx-convertor.command",
    "install-ffmpeg.bat", "install-ffmpeg.ps1",
)
MODULES = (
    "__init__", "__main__", "cli", "ui", "command_builder", "converter",
    "sequence", "ffprobe", "media_support", "exr_metadata", "presets",
    "build_player", "player", "player_binary", "preview_controller", "player_trace",
)
RESOURCE_PATTERNS = (
    "seventh_convert/preset_data/*.json",
    "seventh_convert/color_configs/*/config.ocio",
    "seventh_convert/color_configs/*/luts/*.spi1d",
    "seventh_convert/assets/icons/*.png",
)
PLAYER_FILES = (
    "rust/player/Cargo.toml", "rust/player/Cargo.lock",
    "rust/player/rust-toolchain.toml", "rust/player/src/main.rs",
    "rust/player/src/decode.rs",
)


def source_files() -> list[Path]:
    names = [*ROOT_FILES, *PLAYER_FILES,
             *(f"seventh_convert/{name}.py" for name in MODULES),
             "seventh_convert/preset_data/__init__.py",
             "seventh_convert/color_configs/README.md",
             "docs/images/main-window.png", "docs/images/social-preview.jpg"]
    names.append("seventh_convert/bin/seventh-player.exe" if os.name == "nt"
                 else "seventh_convert/bin/seventh-player")
    for pattern in RESOURCE_PATTERNS:
        matches = sorted(ROOT.glob(pattern))
        if not matches:
            raise ValueError(f"No resources found: {pattern}")
        names.extend(str(path.relative_to(ROOT)) for path in matches)
    paths = sorted(set(Path(name) for name in names))
    for path in paths:
        source = ROOT / path
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"Missing file or unsupported symlink: {source}")
    return paths


def verify(paths: list[Path]) -> bool:
    if DESTINATION.is_symlink() or not DESTINATION.is_dir():
        print("publish/ is missing or is a symlink", file=sys.stderr)
        return False
    actual = {path.relative_to(DESTINATION) for path in DESTINATION.rglob("*") if path.is_file()}
    expected = set(paths)
    problems = [f"Missing: {path}" for path in sorted(expected - actual)]
    problems += [f"Unexpected: {path}" for path in sorted(actual - expected)]
    for path in sorted(expected & actual):
        target = DESTINATION / path
        if target.is_symlink() or target.read_bytes() != (ROOT / path).read_bytes():
            problems.append(f"Different: {path}")
        elif os.name != "nt" and (target.stat().st_mode & 0o777) != ((ROOT / path).stat().st_mode & 0o777):
            problems.append(f"Different permissions: {path}")
    for path in DESTINATION.rglob("*"):
        if path.is_symlink() or (path.is_dir() and not any(path.relative_to(DESTINATION) in item.parents for item in expected)):
            problems.append(f"Unexpected directory or symlink: {path.relative_to(DESTINATION)}")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return False
    print(f"publish/: {len(paths)} files verified; no extra files or directories")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Compare publish with sources without changing it")
    args = parser.parse_args()
    try:
        if DESTINATION.is_symlink():
            raise ValueError("publish/ must not be a symlink")
        if not args.check:
            subprocess.run([sys.executable, "-m", "seventh_convert.build_player"], cwd=ROOT, check=True)
        paths = source_files()
        if args.check:
            return 0 if verify(paths) else 1
        # Stage completely before replacing the previous generated output.
        # A failed copy/build leaves the previous publish intact.
        with tempfile.TemporaryDirectory(prefix=".publish-", dir=ROOT) as temporary:
            staging = Path(temporary) / "new"
            staging.mkdir()
            for path in paths:
                target = staging / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / path, target)
            previous = Path(temporary) / "previous"
            if DESTINATION.exists():
                DESTINATION.rename(previous)
            try:
                staging.rename(DESTINATION)
            except OSError:
                if previous.exists():
                    previous.rename(DESTINATION)
                raise
        return 0 if verify(paths) else 1
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Publish preparation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
