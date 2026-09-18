"""Settings, read once from the environment (and a .env file if present)."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    lmstudio_base_url: str
    lmstudio_api_key: str
    lmstudio_model: str
    lmstudio_timeout: float
    database_path: Path
    date_order: str
    max_image_edge: int


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def load_settings() -> Settings:
    db_path = Path(_env("DATABASE_PATH", "receipts.db"))
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path
    return Settings(
        lmstudio_base_url=_env("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/"),
        lmstudio_api_key=_env("LMSTUDIO_API_KEY", "lm-studio"),
        lmstudio_model=_env("LMSTUDIO_MODEL", "google/gemma-4-12b"),
        lmstudio_timeout=float(_env("LMSTUDIO_TIMEOUT", "300")),
        database_path=db_path,
        date_order=_env("DATE_ORDER", "day").lower(),
        max_image_edge=int(_env("MAX_IMAGE_EDGE", "1600")),
    )


settings = load_settings()
