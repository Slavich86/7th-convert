#!/usr/bin/env bash
set -u

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR" || exit 1

missing=0

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$1" >&2
    missing=1
  fi
}

require_python_module() {
  if ! python3 - "$1" >/dev/null 2>&1 <<'PY'
import importlib
import sys

importlib.import_module(sys.argv[1])
PY
  then
    printf 'Missing required Python module: %s\n' "$1" >&2
    missing=1
  fi
}

require_command python3
require_command ffmpeg
require_command ffprobe

if command -v python3 >/dev/null 2>&1; then
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
  python3 -m pip install -r requirements.txt'

  printf '\n%s\n\n' "$message" >&2

  if command -v notify-send >/dev/null 2>&1; then
    notify-send "7th VFX convertor dependencies missing" "$message"
  fi

  exit 1
fi

exec python3 -m seventh_convert.ui "$@"
