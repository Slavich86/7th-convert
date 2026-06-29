#!/usr/bin/env bash
set -u

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR" || exit 1

missing=0
APP_PYTHON="python3"
VENV_DIR="$APP_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
REQUIREMENTS_FILE="$APP_DIR/requirements.txt"
REQUIREMENTS_MARKER="$VENV_DIR/.requirements.sha256"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$1" >&2
    missing=1
  fi
}

require_python_module() {
  if ! "$APP_PYTHON" - "$1" >/dev/null 2>&1 <<'PY'
import importlib
import sys

importlib.import_module(sys.argv[1])
PY
  then
    printf 'Missing required Python module: %s\n' "$1" >&2
    missing=1
  fi
}

requirements_hash() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$REQUIREMENTS_FILE" | awk '{print $1}'
    return
  fi

  python3 - "$REQUIREMENTS_FILE" <<'PY'
import hashlib
import sys

with open(sys.argv[1], "rb") as handle:
    print(hashlib.sha256(handle.read()).hexdigest())
PY
}

ensure_uv_venv() {
  if ! command -v uv >/dev/null 2>&1; then
    return 1
  fi

  if [ ! -x "$VENV_PYTHON" ]; then
    printf 'Creating Python virtual environment with uv: %s\n' "$VENV_DIR"
    uv venv "$VENV_DIR" --python python3 || return 1
  fi

  expected_hash="$(requirements_hash)"
  current_hash=""
  if [ -f "$REQUIREMENTS_MARKER" ]; then
    current_hash="$(cat "$REQUIREMENTS_MARKER")"
  fi

  if [ "$expected_hash" != "$current_hash" ]; then
    printf 'Installing Python dependencies into %s with uv pip-compatible installer\n' "$VENV_DIR"
    uv pip install --python "$VENV_PYTHON" --link-mode=copy -r "$REQUIREMENTS_FILE" || return 1
    printf '%s\n' "$expected_hash" > "$REQUIREMENTS_MARKER"
  fi

  APP_PYTHON="$VENV_PYTHON"
  return 0
}

require_command python3
require_command ffmpeg
require_command ffprobe

if [ "$missing" -eq 0 ]; then
  if ! ensure_uv_venv; then
    printf 'uv is not available or failed to prepare .venv; falling back to system python3.\n' >&2
    APP_PYTHON="python3"
  fi
fi

if command -v "$APP_PYTHON" >/dev/null 2>&1 || [ -x "$APP_PYTHON" ]; then
  require_python_module numpy
  require_python_module PySide6
  require_python_module PyOpenColorIO
  require_python_module OpenEXR
fi

if [ "$missing" -ne 0 ]; then
  message='Install system dependencies first:
  Fedora: sudo dnf install ffmpeg-free
  Ubuntu/Debian: sudo apt update && sudo apt install ffmpeg
  Arch/Manjaro: sudo pacman -S ffmpeg

Install Python dependencies:
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ./7th-vfx-convertor.sh

Fallback without uv:
  python3 -m pip install -r requirements.txt'

  printf '\n%s\n\n' "$message" >&2

  if command -v notify-send >/dev/null 2>&1; then
    notify-send "7th VFX convertor dependencies missing" "$message"
  fi

  exit 1
fi

exec "$APP_PYTHON" -m seventh_convert.ui "$@"
