#!/bin/sh
set -eu
SOURCE="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
MANAGER="$SOURCE/tools/portable_manager.py"
VENV_PYTHON="$SOURCE/.venv/bin/python"

if [ -x "$VENV_PYTHON" ]; then
  exec "$VENV_PYTHON" "$MANAGER" install --source "$SOURCE" "$@"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Fehler: uv ist für die Portable-Erstinstallation erforderlich, damit eine app-eigene Python-Runtime eingerichtet werden kann." >&2
  exit 1
fi

export UV_PROJECT_ENVIRONMENT="$SOURCE/.venv"
exec uv run --locked --project "$SOURCE" --python 3.12 python "$MANAGER" install --source "$SOURCE" "$@"
