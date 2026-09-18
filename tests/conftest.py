import dataclasses
import io
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def photo_bytes() -> bytes:
    """A small JPEG standing in for a receipt photo."""
    image = Image.new("RGB", (900, 1400), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    test_settings = dataclasses.replace(main.settings, database_path=tmp_path / "test.db")
    monkeypatch.setattr(main, "settings", test_settings)

    with TestClient(main.app) as test_client:  # startup configures the temp database
        yield test_client
