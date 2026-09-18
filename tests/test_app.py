"""End-to-end tests with the model stubbed out - LM Studio is never called."""
import json

import pytest

from app import extractor, main

MODEL_REPLY = json.dumps(
    {
        "merchant": "Caffe Milano",
        "merchant_address": "12 High Street, London",
        "date": "03/04/2025",
        "time": "09:12",
        "currency": "GBP",
        "subtotal": 8.40,
        "tax": 1.68,
        "tip": None,
        "total": 10.08,
        "payment_method": "Visa contactless",
        "category": "restaurant",
        "line_items": [
            {"description": "Flat white", "quantity": 2, "unit_price": 3.20, "line_total": 6.40},
            {"description": "Cornetto", "quantity": 1, "unit_price": 2.00, "line_total": 2.00},
        ],
    }
)


@pytest.fixture
def stub_model(monkeypatch):
    calls = []

    def fake_extract(**kwargs):
        calls.append(kwargs)
        return MODEL_REPLY, "json_schema"

    monkeypatch.setattr(main.extractor, "extract", fake_extract)
    return calls


def upload(client, photo_bytes, notes=""):
    return client.post(
        "/upload",
        files={"file": ("receipt.jpg", photo_bytes, "image/jpeg")},
        data={"notes": notes},
        follow_redirects=False,
    )


def test_upload_extracts_and_stores(client, photo_bytes, stub_model):
    response = upload(client, photo_bytes)
    assert response.status_code == 303
    location = response.headers["location"]

    page = client.get(location)
    assert page.status_code == 200
    assert "Caffe Milano" in page.text
    assert "10.08" in page.text
    assert "Flat white" in page.text
    # The date on the receipt is day-first, so 03/04 is 3 April.
    assert "2025-04-03" in page.text
    assert len(stub_model) == 1


def test_photo_is_stored_and_served(client, photo_bytes, stub_model):
    upload(client, photo_bytes)
    listing = client.get("/")
    assert "Caffe Milano" in listing.text

    image = client.get("/images/1")
    assert image.status_code == 200
    assert image.content == photo_bytes

    thumb = client.get("/images/1?thumb=true")
    assert thumb.status_code == 200
    assert 0 < len(thumb.content) < len(photo_bytes)


def test_identical_photo_is_stored_once(client, photo_bytes, stub_model):
    upload(client, photo_bytes)
    upload(client, photo_bytes)
    from app import db

    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM images").fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) c FROM receipts").fetchone()["c"] == 2


def test_edits_are_saved(client, photo_bytes, stub_model):
    upload(client, photo_bytes)
    response = client.post(
        "/receipts/1",
        data={
            "merchant": "Caffè Milano",
            "purchased_on": "2025-04-03",
            "purchased_at": "09:12",
            "currency": "GBP",
            "total": "11,08",
            "tax": "1.68",
            "category": "restaurant",
            "notes": "Team breakfast",
            "item_description": ["Flat white", "Cornetto", ""],
            "item_quantity": ["2", "1", ""],
            "item_unit_price": ["3.20", "2.00", ""],
            "item_line_total": ["6.40", "2.00", ""],
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Caffè Milano" in response.text
    assert "Team breakfast" in response.text
    assert "11.08" in response.text  # comma decimal accepted from the form

    from app import db

    with db.connect() as conn:
        # The blank third row is dropped rather than stored as an empty item.
        assert conn.execute("SELECT COUNT(*) c FROM line_items").fetchone()["c"] == 2


def test_delete_removes_receipt_and_photo(client, photo_bytes, stub_model):
    upload(client, photo_bytes)
    client.post("/receipts/1/delete", follow_redirects=False)

    from app import db

    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM receipts").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM images").fetchone()["c"] == 0


def test_shared_photo_survives_deleting_one_receipt(client, photo_bytes, stub_model):
    upload(client, photo_bytes)
    upload(client, photo_bytes)  # same bytes, so both receipts share image 1
    client.post("/receipts/1/delete", follow_redirects=False)

    from app import db

    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM images").fetchone()["c"] == 1
    assert client.get("/images/1").status_code == 200


def test_model_failure_still_saves_the_photo(client, photo_bytes, monkeypatch):
    def boom(**kwargs):
        raise extractor.ExtractionError("Could not reach LM Studio at http://127.0.0.1:1234/v1")

    monkeypatch.setattr(main.extractor, "extract", boom)

    response = upload(client, photo_bytes)
    assert response.status_code == 303
    page = client.get(response.headers["location"])
    assert "could not read this one" in page.text.lower()
    assert "Could not reach LM Studio" in page.text

    image = client.get("/images/1")
    assert image.content == photo_bytes


def test_unusable_model_output_is_recorded_not_crashed(client, photo_bytes, monkeypatch):
    monkeypatch.setattr(
        main.extractor, "extract", lambda **kwargs: ("I'm sorry, I can't read that.", "prompt")
    )
    response = upload(client, photo_bytes)
    page = client.get(response.headers["location"])
    assert page.status_code == 200
    assert "not with usable JSON" in page.text


def test_non_image_upload_is_rejected(client, stub_model):
    response = client.post(
        "/upload",
        files={"file": ("notes.txt", b"this is not an image", "text/plain")},
        data={"notes": ""},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "does not look like an image" in response.text
    assert len(stub_model) == 0


def test_csv_exports(client, photo_bytes, stub_model):
    upload(client, photo_bytes)

    receipts = client.get("/export/receipts.csv")
    assert receipts.status_code == 200
    assert "text/csv" in receipts.headers["content-type"]
    assert "Caffe Milano" in receipts.text
    assert receipts.text.splitlines()[0].startswith("id,purchased_on")

    items = client.get("/export/line-items.csv")
    assert "Flat white" in items.text
    assert len(items.text.strip().splitlines()) == 3  # header + 2 items


def test_search_filters_the_list(client, photo_bytes, stub_model):
    upload(client, photo_bytes)
    assert "Caffe Milano" in client.get("/?q=milano").text
    assert "Caffe Milano" not in client.get("/?q=tesco").text


def test_reextract_reruns_the_model(client, photo_bytes, stub_model):
    upload(client, photo_bytes)
    assert len(stub_model) == 1
    client.post("/receipts/1/reextract", follow_redirects=False)
    assert len(stub_model) == 2


def test_missing_receipt_is_404(client):
    assert client.get("/receipts/999").status_code == 404
