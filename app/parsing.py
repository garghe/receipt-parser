"""Turning whatever the model said into values we can put in columns.

Local vision models are loose: they wrap JSON in prose, write "£12.50" or
"12,50", and give dates in whatever format the receipt used. Everything here
exists to absorb that.
"""
import json
import re
from datetime import date, datetime
from typing import Any

CURRENCY_SYMBOLS = {
    "£": "GBP",
    "$": "USD",
    "€": "EUR",
    "¥": "JPY",
    "₹": "INR",
    "CHF": "CHF",
}

_NUMBER_JUNK = re.compile(r"[^0-9,.\-]")


def extract_json_object(text: str) -> dict[str, Any]:
    """Pull the first complete JSON object out of a model response.

    Handles bare JSON, ```json fences, and JSON with commentary around it.
    """
    if not text:
        raise ValueError("empty response")

    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL | re.IGNORECASE)
    candidates = [fenced.group(1)] if fenced else []
    candidates.append(text)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate.strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        block = _first_balanced_object(candidate)
        if block is not None:
            try:
                parsed = json.loads(block)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON object found in model response")


def _first_balanced_object(text: str) -> str | None:
    """Scan for a brace-balanced object, ignoring braces inside strings."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def parse_amount(value: Any) -> float | None:
    """Read a money value written any of the ways a receipt might write it."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None

    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    cleaned = _NUMBER_JUNK.sub("", text)
    if not cleaned:
        return None
    cleaned = cleaned.lstrip("-")

    last_comma = cleaned.rfind(",")
    last_dot = cleaned.rfind(".")
    if last_comma > last_dot:
        # European style: 1.234,56 -> the comma is the decimal separator.
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")

    # A stray trailing separator ("12." or "12,") should not blow up.
    cleaned = cleaned.rstrip(".")
    if not cleaned:
        return None
    try:
        amount = float(cleaned)
    except ValueError:
        return None
    return -amount if negative else amount


def parse_quantity(value: Any) -> float | None:
    return parse_amount(value)


def parse_currency(value: Any, fallback_text: str = "") -> str | None:
    """Normalise to a 3-letter code, falling back to a symbol seen on the receipt."""
    if value:
        text = str(value).strip()
        if re.fullmatch(r"[A-Za-z]{3}", text):
            return text.upper()
        for symbol, code in CURRENCY_SYMBOLS.items():
            if symbol in text:
                return code
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in fallback_text:
            return code
    return None


_DATE_PATTERNS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d %Y",
    "%b %d %Y",
    "%d %B %y",
    "%d %b %y",
)


def parse_date(value: Any, day_first: bool = True) -> str | None:
    """Return an ISO date string, or None if we cannot read it confidently."""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text, flags=re.IGNORECASE)
    text = text.replace(",", " ")
    text = re.sub(r"\s+", " ", text).strip()

    numeric = re.match(r"^(\d{1,4})[-/.](\d{1,2})[-/.](\d{2,4})$", text)
    if numeric:
        a, b, c = (int(part) for part in numeric.groups())
        if len(numeric.group(1)) == 4:
            year, month, day = a, b, c
        else:
            year = _expand_year(c)
            if day_first:
                day, month = a, b
            else:
                month, day = a, b
            # If the "day" can only be a month, the model swapped them.
            if month > 12 and day <= 12:
                day, month = month, day
        try:
            return date(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return None

    for pattern in _DATE_PATTERNS:
        try:
            return datetime.strptime(text, pattern).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _expand_year(year: int) -> int:
    if year >= 100:
        return year
    return 2000 + year if year < 70 else 1900 + year


def parse_time(value: Any) -> str | None:
    """Return HH:MM (24h), accepting 14:05, 2:05 PM, 14:05:33."""
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    match = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?", text)
    if not match:
        return None
    hour, minute, _seconds, meridiem = match.groups()
    hour_i, minute_i = int(hour), int(minute)
    if meridiem == "PM" and hour_i < 12:
        hour_i += 12
    elif meridiem == "AM" and hour_i == 12:
        hour_i = 0
    if not (0 <= hour_i <= 23 and 0 <= minute_i <= 59):
        return None
    return f"{hour_i:02d}:{minute_i:02d}"


def clean_text(value: Any, max_length: int = 500) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(part) for part in value if part)
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text or text.lower() in {"null", "none", "n/a", "unknown", "-"}:
        return None
    return text[:max_length]


def normalise_extraction(payload: dict[str, Any], day_first: bool = True) -> dict[str, Any]:
    """Map the model's JSON onto our columns, coercing every value."""
    raw_text = json.dumps(payload, ensure_ascii=False)

    def pick(*names: str) -> Any:
        for name in names:
            if name in payload and payload[name] not in (None, ""):
                return payload[name]
        return None

    fields = {
        "merchant": clean_text(pick("merchant", "merchant_name", "store", "vendor")),
        "merchant_address": clean_text(pick("merchant_address", "address")),
        "merchant_vat_id": clean_text(pick("merchant_vat_id", "vat_id", "vat_number", "tax_id"), 60),
        "purchased_on": parse_date(pick("date", "purchase_date", "purchased_on"), day_first),
        "purchased_at": parse_time(pick("time", "purchase_time", "purchased_at")),
        "currency": parse_currency(pick("currency"), raw_text),
        "subtotal": parse_amount(pick("subtotal", "net_total")),
        "tax": parse_amount(pick("tax", "vat", "tax_total")),
        "tip": parse_amount(pick("tip", "gratuity", "service_charge")),
        "total": parse_amount(pick("total", "total_amount", "grand_total", "amount_due")),
        "payment_method": clean_text(pick("payment_method", "payment", "paid_with"), 80),
        "category": clean_text(pick("category"), 80),
        "notes": clean_text(pick("notes", "comment")),
    }

    items_raw = payload.get("line_items") or payload.get("items") or []
    line_items: list[dict[str, Any]] = []
    if isinstance(items_raw, list):
        for entry in items_raw:
            if isinstance(entry, str):
                entry = {"description": entry}
            if not isinstance(entry, dict):
                continue
            item = {
                "description": clean_text(
                    entry.get("description") or entry.get("name") or entry.get("item")
                ),
                "quantity": parse_quantity(entry.get("quantity") or entry.get("qty")),
                "unit_price": parse_amount(entry.get("unit_price") or entry.get("price")),
                "line_total": parse_amount(
                    entry.get("line_total") or entry.get("total") or entry.get("amount")
                ),
            }
            if any(value is not None for value in item.values()):
                line_items.append(item)

    return {"fields": fields, "line_items": line_items}


def totals_mismatch(fields: dict[str, Any], line_items: list[dict[str, Any]]) -> str | None:
    """Warn when the line items do not add up to the stated total.

    This is the single most common way a local model gets a receipt wrong, and
    it is cheap to detect.
    """
    total = fields.get("total")
    if total is None or not line_items:
        return None
    summed = sum(item["line_total"] for item in line_items if item.get("line_total") is not None)
    if summed == 0:
        return None
    expected = summed + (fields.get("tax") or 0) + (fields.get("tip") or 0)
    if abs(expected - total) <= max(0.02, abs(total) * 0.01):
        return None
    if abs(summed - total) <= max(0.02, abs(total) * 0.01):
        return None
    return (
        f"Line items add up to {summed:.2f} but the total reads {total:.2f}. "
        "Worth checking against the photo."
    )
