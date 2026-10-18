#!/bin/bash
# Finder entry point; keep the shared launcher beside this file.
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
/bin/bash "$APP_DIR/7th-vfx-convertor.sh" "$@"
status=$?
if [ "$status" -ne 0 ]; then
  printf '\nConverter stopped with error %s. Press Return to close.\n' "$status" >&2
  read -r _answer
fi
exit "$status"
