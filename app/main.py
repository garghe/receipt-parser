"""FastAPI app: upload a receipt photo, extract it with a local model, keep it."""
import csv
import io
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, extractor, images, parsing
from .config import BASE_DIR, settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("receipt-parser")

MAX_UPLOAD_BYTES = 25 * 1024 * 1024

@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.configure(settings.database_path)
    log.info("Database: %s", settings.database_path)
    log.info("LM Studio: %s (model %s)", settings.lmstudio_base_url, settings.lmstudio_model)
    yield


app = FastAPI(title="Receipt Parser", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


def _money(value: Any) -> str:
    return "" if value is None else f"{float(value):.2f}"


templates.env.filters["money"] = _money


@app.get("/", response_class=HTMLResponse)
def index(request: Request, q: str = "", error: str = "", notice: str = ""):
    with db.connect() as conn:
        receipts = db.list_receipts(conn, q.strip())
    total = sum(row["total"] or 0 for row in receipts)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "receipts": receipts,
            "query": q,
            "error": error,
            "notice": notice,
            "grand_total": total,
            "model": settings.lmstudio_model,
        },
    )


@app.post("/upload")
async def upload(file: UploadFile, notes: str = Form("")):
    if not file or not file.filename:
        return RedirectResponse("/?error=Choose+a+photo+first.", status_code=303)

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        return RedirectResponse(
            f"/?error=That+file+is+over+{MAX_UPLOAD_BYTES // (1024 * 1024)}MB.", status_code=303
        )

    try:
        prepared = images.prepare(data, file.content_type or "", settings.max_image_edge)
    except ValueError as exc:
        return RedirectResponse(f"/?error={_quote(str(exc))}", status_code=303)

    raw_response = ""
    extraction_error = None
    parsed: dict[str, Any] = {"fields": {}, "line_items": []}
    try:
        raw_response, mode = extractor.extract(
            image_data_url=prepared.model_data_url,
            base_url=settings.lmstudio_base_url,
            api_key=settings.lmstudio_api_key,
            model=settings.lmstudio_model,
            timeout=settings.lmstudio_timeout,
        )
        log.info("Extracted via %s mode", mode)
        payload = parsing.extract_json_object(raw_response)
        parsed = parsing.normalise_extraction(payload, day_first=settings.date_order == "day")
    except extractor.ExtractionError as exc:
        extraction_error = str(exc)
        log.error("Extraction failed: %s", exc)
    except ValueError as exc:
        extraction_error = f"The model replied but not with usable JSON: {exc}"
        log.error("%s", extraction_error)

    if notes.strip():
        parsed["fields"]["notes"] = notes.strip()

    with db.connect() as conn:
        image_id = db.store_image(
            conn,
            sha256=prepared.sha256,
            mime_type=prepared.mime_type,
            filename=file.filename,
            data=prepared.data,
            thumbnail=prepared.thumbnail,
            width=prepared.width,
            height=prepared.height,
        )
        receipt_id = db.create_receipt(
            conn,
            image_id=image_id,
            fields=parsed["fields"],
            line_items=parsed["line_items"],
            model=settings.lmstudio_model,
            raw_response=raw_response,
            extraction_error=extraction_error,
        )
    return RedirectResponse(f"/receipts/{receipt_id}", status_code=303)


@app.get("/receipts/{receipt_id}", response_class=HTMLResponse)
def detail(request: Request, receipt_id: int, notice: str = ""):
    with db.connect() as conn:
        receipt = db.get_receipt(conn, receipt_id)
        if receipt is None:
            raise HTTPException(status_code=404, detail="No such receipt")
        line_items = db.get_line_items(conn, receipt_id)

    warning = parsing.totals_mismatch(
        dict(receipt), [dict(item) for item in line_items]
    )
    return templates.TemplateResponse(
        request,
        "detail.html",
        {
            "receipt": receipt,
            "line_items": line_items,
            "warning": warning,
            "notice": notice,
        },
    )


@app.post("/receipts/{receipt_id}")
async def save(request: Request, receipt_id: int):
    form = await request.form()
    fields = {
        "merchant": parsing.clean_text(form.get("merchant")),
        "merchant_address": parsing.clean_text(form.get("merchant_address")),
        "merchant_vat_id": parsing.clean_text(form.get("merchant_vat_id"), 60),
        "purchased_on": parsing.parse_date(
            form.get("purchased_on"), day_first=settings.date_order == "day"
        ),
        "purchased_at": parsing.parse_time(form.get("purchased_at")),
        "currency": parsing.parse_currency(form.get("currency")),
        "subtotal": parsing.parse_amount(form.get("subtotal")),
        "tax": parsing.parse_amount(form.get("tax")),
        "tip": parsing.parse_amount(form.get("tip")),
        "total": parsing.parse_amount(form.get("total")),
        "payment_method": parsing.clean_text(form.get("payment_method"), 80),
        "category": parsing.clean_text(form.get("category"), 80),
        "notes": parsing.clean_text(form.get("notes")),
    }

    descriptions = form.getlist("item_description")
    quantities = form.getlist("item_quantity")
    unit_prices = form.getlist("item_unit_price")
    line_totals = form.getlist("item_line_total")

    line_items = []
    for index, description in enumerate(descriptions):
        item = {
            "description": parsing.clean_text(description),
            "quantity": parsing.parse_quantity(_at(quantities, index)),
            "unit_price": parsing.parse_amount(_at(unit_prices, index)),
            "line_total": parsing.parse_amount(_at(line_totals, index)),
        }
        if any(value is not None for value in item.values()):
            line_items.append(item)

    with db.connect() as conn:
        if db.get_receipt(conn, receipt_id) is None:
            raise HTTPException(status_code=404, detail="No such receipt")
        db.update_receipt(conn, receipt_id, fields=fields, line_items=line_items)
    return RedirectResponse(f"/receipts/{receipt_id}?notice=Saved.", status_code=303)


@app.post("/receipts/{receipt_id}/reextract")
def reextract(receipt_id: int):
    """Run the model over the stored photo again - useful after swapping models."""
    with db.connect() as conn:
        receipt = db.get_receipt(conn, receipt_id)
        if receipt is None:
            raise HTTPException(status_code=404, detail="No such receipt")
        image = db.get_image(conn, receipt["image_id"])

    prepared = images.prepare(image["blob"], image["mime_type"], settings.max_image_edge)
    try:
        raw_response, _mode = extractor.extract(
            image_data_url=prepared.model_data_url,
            base_url=settings.lmstudio_base_url,
            api_key=settings.lmstudio_api_key,
            model=settings.lmstudio_model,
            timeout=settings.lmstudio_timeout,
        )
        payload = parsing.extract_json_object(raw_response)
        parsed = parsing.normalise_extraction(payload, day_first=settings.date_order == "day")
    except (extractor.ExtractionError, ValueError) as exc:
        return RedirectResponse(
            f"/receipts/{receipt_id}?notice={_quote(str(exc))}", status_code=303
        )

    with db.connect() as conn:
        db.update_receipt(
            conn, receipt_id, fields=parsed["fields"], line_items=parsed["line_items"]
        )
        conn.execute(
            "UPDATE receipts SET raw_response = ?, model = ?, extraction_error = NULL WHERE id = ?",
            (raw_response, settings.lmstudio_model, receipt_id),
        )
    return RedirectResponse(
        f"/receipts/{receipt_id}?notice=Re-read+with+{_quote(settings.lmstudio_model)}.",
        status_code=303,
    )


@app.post("/receipts/{receipt_id}/delete")
def delete(receipt_id: int):
    with db.connect() as conn:
        db.delete_receipt(conn, receipt_id)
    return RedirectResponse("/?notice=Receipt+deleted.", status_code=303)


@app.get("/images/{image_id}")
def image(image_id: int, thumb: bool = False):
    with db.connect() as conn:
        row = db.get_image(conn, image_id, thumbnail=thumb)
    if row is None or row["blob"] is None:
        raise HTTPException(status_code=404, detail="No such image")
    mime = "image/jpeg" if thumb else row["mime_type"]
    return Response(
        content=row["blob"],
        media_type=mime,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.get("/export/receipts.csv")
def export_receipts():
    columns = [
        "id", "purchased_on", "purchased_at", "merchant", "merchant_address",
        "merchant_vat_id", "category", "currency", "subtotal", "tax", "tip",
        "total", "payment_method", "notes", "created_at",
    ]
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(columns)} FROM receipts "
            "ORDER BY COALESCE(purchased_on, '') DESC, id DESC"
        ).fetchall()
    return _csv_response(columns, [[row[name] for name in columns] for row in rows], "receipts.csv")


@app.get("/export/line-items.csv")
def export_line_items():
    columns = [
        "receipt_id", "purchased_on", "merchant", "currency",
        "description", "quantity", "unit_price", "line_total",
    ]
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT li.receipt_id, r.purchased_on, r.merchant, r.currency,
                      li.description, li.quantity, li.unit_price, li.line_total
               FROM line_items li JOIN receipts r ON r.id = li.receipt_id
               ORDER BY li.receipt_id DESC, li.position"""
        ).fetchall()
    return _csv_response(
        columns, [[row[name] for name in columns] for row in rows], "line-items.csv"
    )


@app.get("/models", response_class=HTMLResponse)
def models(request: Request):
    """Diagnostics: is LM Studio up, and what exactly is it serving?"""
    available: list[str] = []
    error = None
    try:
        available = extractor.list_models(settings.lmstudio_base_url, settings.lmstudio_api_key)
    except extractor.ExtractionError as exc:
        error = str(exc)
    return templates.TemplateResponse(
        request,
        "models.html",
        {
            "available": available,
            "error": error,
            "configured": settings.lmstudio_model,
            "base_url": settings.lmstudio_base_url,
            "configured_is_available": settings.lmstudio_model in available,
        },
    )


def _csv_response(columns: list[str], rows: list[list[Any]], filename: str) -> StreamingResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    writer.writerows(rows)
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _at(values: list[str], index: int) -> str | None:
    return values[index] if index < len(values) else None


def _quote(text: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(text[:300])
