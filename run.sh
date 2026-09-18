#!/usr/bin/env bash
# Sets up the virtualenv if needed, then starts the app. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

# Pick an interpreter. Override with: PYTHON=python3.14 ./run.sh
# The default prefers a version whose wheels are widely published - on a brand
# new Python, packages with C extensions (Pillow above all) may have no wheel
# yet, and pip then tries to compile them from source.
if [ -n "${PYTHON:-}" ]; then
  PYTHON_BIN="$PYTHON"
else
  PYTHON_BIN=""
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
  [ -n "$PYTHON_BIN" ] || { echo "No python3 found on PATH." >&2; exit 1; }
fi

venv_python() {
  if [ -x .venv/bin/python ]; then echo .venv/bin/python
  elif [ -x .venv/Scripts/python.exe ]; then echo .venv/Scripts/python.exe   # Windows
  else echo ""; fi
}

if [ ! -d .venv ]; then
  echo "Creating .venv with $("$PYTHON_BIN" --version)..."
  "$PYTHON_BIN" -m venv .venv
fi

VENV_PY="$(venv_python)"
if [ -z "$VENV_PY" ]; then
  echo "The .venv directory exists but holds no interpreter. Delete it and re-run:" >&2
  echo "    rm -rf .venv && ./run.sh" >&2
  exit 1
fi

# Call the venv's python directly, so this works whether or not the venv is
# activated in your shell.
if ! "$VENV_PY" -c "import uvicorn, fastapi, PIL, httpx, jinja2, multipart, dotenv" 2>/dev/null; then
  echo "Installing dependencies into .venv ($("$VENV_PY" --version))..."
  "$VENV_PY" -m pip install --quiet --upgrade pip
  # --prefer-binary: take an older release that ships a wheel over a newer one
  # that would have to be compiled.
  if ! "$VENV_PY" -m pip install --quiet --prefer-binary -r requirements.txt; then
    echo >&2
    echo "Dependency installation failed." >&2
    echo >&2
    echo "If the error mentions building Pillow from source (\"could not be found" >&2
    echo "for jpeg\"), your Python is newer than the published wheels. Either:" >&2
    echo >&2
    echo "  1. Use an older interpreter:  rm -rf .venv && PYTHON=python3.12 ./run.sh" >&2
    echo "  2. Or install the C libraries: brew install libjpeg zlib libtiff webp" >&2
    echo >&2
    exit 1
  fi
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example - check LMSTUDIO_MODEL matches your loaded model."
fi

exec "$VENV_PY" -m app
