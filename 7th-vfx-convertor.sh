#!/usr/bin/env bash
set -u

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR" || exit 1

# Finder does not necessarily inherit the shell's Homebrew/Cargo/uv PATH.
if [ "$(uname -s)" = Darwin ]; then
  if [ "$(uname -m)" = arm64 ]; then
    export PATH="/opt/homebrew/bin:$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
  else
    export PATH="/usr/local/bin:$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
  fi
fi

VENV_DIR="$APP_DIR/.venv"
APP_PYTHON="$VENV_DIR/bin/python"
REQUIREMENTS_FILE="$APP_DIR/requirements.txt"
REQUIREMENTS_MARKER="$VENV_DIR/.requirements.sha256"

for tool in ffmpeg ffprobe; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$tool" >&2
    printf 'macOS: brew install ffmpeg\nLinux: install ffmpeg using your package manager.\n' >&2
    exit 1
  fi
done

if [ ! -x "$APP_PYTHON" ]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv "$VENV_DIR" --python '>=3.11' || exit $?
  else
    if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
      printf 'Python 3.11+ is required. macOS: brew install python@3.13 uv\n' >&2
      exit 1
    fi
    python3 -m venv "$VENV_DIR" || exit $?
    "$APP_PYTHON" -m pip install -r "$REQUIREMENTS_FILE" || exit $?
  fi
fi

if ! "$APP_PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  printf 'The local .venv requires Python 3.11+. Recreate it on this computer.\n' >&2
  exit 1
fi

if command -v uv >/dev/null 2>&1; then
  expected_hash="$("$APP_PYTHON" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$REQUIREMENTS_FILE")" || exit $?
  current_hash=""
  if [ -f "$REQUIREMENTS_MARKER" ]; then
    current_hash="$(< "$REQUIREMENTS_MARKER")"
  fi
  if [ "$expected_hash" != "$current_hash" ]; then
    uv pip install --python "$APP_PYTHON" --link-mode=copy -r "$REQUIREMENTS_FILE" || exit $?
    printf '%s\n' "$expected_hash" > "$REQUIREMENTS_MARKER"
  fi
fi

if ! "$APP_PYTHON" -c 'import numpy, PySide6, PyOpenColorIO, OpenEXR'; then
  printf 'Dependencies are missing in %s. Install with:\n  "%s" -m pip install -r "%s"\n' "$VENV_DIR" "$APP_PYTHON" "$REQUIREMENTS_FILE" >&2
  exit 1
fi

"$APP_PYTHON" -m seventh_convert.build_player --if-available || exit $?
exec "$APP_PYTHON" -m seventh_convert.ui "$@"
