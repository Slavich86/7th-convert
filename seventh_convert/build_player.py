"""Build/install the native preview executable using the platform's Cargo toolchain."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .player_binary import compatible_player


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--if-available", action="store_true", help="Keep Qt preview if Cargo is unavailable")
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    manifest = package.parent / "rust" / "player" / "Cargo.toml"
    name = "seventh-player.exe" if os.name == "nt" else "seventh-player"
    destination = package / "bin" / name
    if not manifest.exists():
        print("Rust sources unavailable. Use a platform build containing seventh_convert/bin/seventh-player.")
        return 0 if compatible_player(destination) or args.if_available else 1
    sources = [manifest, manifest.with_name("Cargo.lock"), *manifest.parent.glob("src/*.rs")]
    toolchain = manifest.with_name("rust-toolchain.toml")
    if toolchain.exists():
        sources.append(toolchain)
    if compatible_player(destination) and all(path.stat().st_mtime <= destination.stat().st_mtime for path in sources):
        return 0
    if not shutil.which("cargo"):
        print("Rust preview needs a build for this OS/CPU: Cargo is missing. Qt preview remains available.")
        return 0 if args.if_available else 1
    completed = subprocess.run(["cargo", "build", "--release", "--locked", "--manifest-path", str(manifest)], check=False, cwd=manifest.parent)
    if completed.returncode:
        print("Rust preview build failed. See Cargo diagnostics above.")
        return completed.returncode
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Linux may still be running the previous executable in an open converter.
    # Replace its directory entry instead of truncating that running binary.
    fd, temporary = tempfile.mkstemp(prefix=f".{name}-", dir=destination.parent)
    os.close(fd)
    staged = Path(temporary)
    try:
        shutil.copy2(manifest.parent / "target" / "release" / name, staged)
        staged.replace(destination)
    finally:
        staged.unlink(missing_ok=True)
    print(f"Rust preview installed: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
