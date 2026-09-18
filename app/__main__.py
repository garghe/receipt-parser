"""Run with: ./run.sh  (or python -m app inside an activated virtualenv)"""
import os
import sys

try:
    import uvicorn
except ModuleNotFoundError:
    sys.exit(
        f"Dependencies are missing from {sys.executable}.\n\n"
        "The most likely cause is that the virtualenv is not active in this shell.\n"
        "Either run ./run.sh, which handles it for you, or:\n\n"
        "    source .venv/bin/activate\n"
        "    pip install -r requirements.txt\n"
        "    python -m app\n"
    )

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        reload=os.environ.get("RELOAD", "").lower() in {"1", "true", "yes"},
    )
