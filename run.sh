#!/usr/bin/env bash
# Sets up the virtualenv if needed, then starts the app. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

if [ ! -d .venv ]; then
  echo "Creating .venv..."
  "$PYTHON" -m venv .venv
fi

# Call the venv's python directly, so this works whether or not the venv is
# activated in your shell.
VENV_PY=".venv/bin/python"
[ -x "$VENV_PY" ] || VENV_PY=".venv/Scripts/python.exe"   # Windows layout

if ! "$VENV_PY" -c "import uvicorn, fastapi, PIL, httpx" 2>/dev/null; then
  echo "Installing dependencies..."
  "$VENV_PY" -m pip install --quiet --upgrade pip
  "$VENV_PY" -m pip install --quiet -r requirements.txt
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example - check LMSTUDIO_MODEL matches your loaded model."
fi

exec "$VENV_PY" -m app
