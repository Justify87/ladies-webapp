#!/bin/sh
set -eu
SOURCE="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
exec python3 "$SOURCE/tools/portable_manager.py" install --source "$SOURCE" "$@"
