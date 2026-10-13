#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

if [ ! -f "$ROOT/current" ]; then
  echo "Fehler: Portable Installation ist noch nicht eingerichtet. Bitte zuerst packaging/install.command ausführen." >&2
  exit 1
fi

CURRENT="$(tr -d '\r\n' < "$ROOT/current")"
RELEASE="$ROOT/releases/$CURRENT"
MANAGER="$RELEASE/tools/portable_manager.py"
VENV_PYTHON="$RELEASE/.venv/bin/python"

if [ ! -f "$MANAGER" ]; then
  echo "Fehler: Aktiver Release $CURRENT ist unvollständig: $MANAGER fehlt." >&2
  exit 1
fi

if [ -x "$VENV_PYTHON" ]; then
  exec "$VENV_PYTHON" "$MANAGER" start --root "$ROOT" "$@"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Fehler: Die app-eigene Python-Runtime fehlt und uv ist nicht verfügbar." >&2
  echo "Bitte uv installieren oder die Portable-Installation erneut einrichten." >&2
  exit 1
fi

export UV_PYTHON_INSTALL_DIR="$ROOT/.runtime/python"
export UV_PROJECT_ENVIRONMENT="$RELEASE/.venv"
mkdir -p "$UV_PYTHON_INSTALL_DIR"
uv python install 3.12 >/dev/null
PYTHON="$(uv python find 3.12)"
exec uv run --locked --project "$RELEASE" --python "$PYTHON" python "$MANAGER" start --root "$ROOT" "$@"
