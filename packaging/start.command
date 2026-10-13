#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
CURRENT="$(tr -d '\r\n' < "$ROOT/current")"
exec python3 "$ROOT/releases/$CURRENT/tools/portable_manager.py" start --root "$ROOT" "$@"
