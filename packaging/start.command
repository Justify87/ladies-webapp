#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

if [ ! -f "$ROOT/current" ]; then
  echo "Fehler: Portable Installation ist noch nicht eingerichtet. Bitte zuerst packaging/install.command ausführen." >&2
  exit 1
fi

CURRENT="$(tr -d '\r\n' < "$ROOT/current")"
RELEASE="$ROOT/releases/$CURRENT"
SOURCE_MANAGER="$RELEASE/tools/launcher_manager.py"
STATE_DIR="$ROOT/.state"
MANAGER="$STATE_DIR/launcher_manager.py"
VENV_PYTHON="$RELEASE/.venv/bin/python"

mkdir -p "$STATE_DIR"
if [ -f "$SOURCE_MANAGER" ]; then
  cp "$SOURCE_MANAGER" "$MANAGER.tmp"
  mv "$MANAGER.tmp" "$MANAGER"
fi

if [ ! -f "$MANAGER" ]; then
  echo "Fehler: Launcher Manager fehlt. Bitte die Portable-Installation mit einem aktuellen Release reparieren." >&2
  exit 1
fi

export PYTHONPATH="$RELEASE${PYTHONPATH:+:$PYTHONPATH}"

if [ -x "$VENV_PYTHON" ]; then
  exec "$VENV_PYTHON" "$MANAGER" launch --root "$ROOT" "$@"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Fehler: Die app-eigene Python-Runtime fehlt und uv ist nicht verfügbar." >&2
  echo "Bitte uv installieren oder die Portable-Installation erneut einrichten." >&2
  exit 1
fi

export UV_PYTHON_INSTALL_DIR="$ROOT/.runtime/python"
mkdir -p "$UV_PYTHON_INSTALL_DIR"
uv python install 3.12 >/dev/null
PYTHON="$(uv python find 3.12)"
exec "$PYTHON" "$MANAGER" launch --root "$ROOT" "$@"
