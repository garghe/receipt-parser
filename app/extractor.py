"""Talks to LM Studio's OpenAI-compatible endpoint.

Two things shape this file. First, Gemma's chat template has no system role,
so the whole instruction goes in the single user turn alongside the image.
Second, JSON-schema mode is not reliable for every model LM Studio can load,
so we ask for it, and fall back to plain prompting if the server refuses.
"""
import json
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

RECEIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "merchant": {"type": ["string", "null"]},
        "merchant_address": {"type": ["string", "null"]},
        "merchant_vat_id": {"type": ["string", "null"]},
        "date": {"type": ["string", "null"]},
        "time": {"type": ["string", "null"]},
        "currency": {"type": ["string", "null"]},
        "subtotal": {"type": ["number", "null"]},
        "tax": {"type": ["number", "null"]},
        "tip": {"type": ["number", "null"]},
        "total": {"type": ["number", "null"]},
        "payment_method": {"type": ["string", "null"]},
        "category": {"type": ["string", "null"]},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": ["string", "null"]},
                    "quantity": {"type": ["number", "null"]},
                    "unit_price": {"type": ["number", "null"]},
                    "line_total": {"type": ["number", "null"]},
                },
            },
        },
    },
    "required": ["merchant", "total", "line_items"],
}

PROMPT = """You are reading a photograph of a shop receipt.

Extract the details and reply with a single JSON object and nothing else - no
explanation, no markdown fence.

Use these keys:
  merchant          shop or restaurant name
  merchant_address  street address printed on the receipt
  merchant_vat_id   VAT / tax number if printed
  date              purchase date exactly as printed on the receipt
  time              purchase time as printed
  currency          three-letter code such as GBP, EUR, USD
  subtotal          amount before tax, as a number
  tax               tax or VAT amount, as a number
  tip               tip or service charge, as a number
  total             final amount paid, as a number
  payment_method    e.g. Visa, cash, contactless
  category          one of: groceries, restaurant, transport, fuel, travel,
                    retail, health, utilities, other
  line_items        array of {description, quantity, unit_price, line_total}

Rules:
- Copy the date exactly as printed; do not reformat or guess it.
- Write amounts as plain numbers with a dot for decimals, no currency symbol.
- Use null for anything you cannot read. Never invent a value.
- Include every purchased line on the receipt, in the order printed."""


class ExtractionError(RuntimeError):
    """Something went wrong reaching or reading the model."""


def list_models(base_url: str, api_key: str, timeout: float = 10.0) -> list[str]:
    """Ask LM Studio which models it is serving, so the user can pick the right id."""
    try:
        response = httpx.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ExtractionError(_connection_hint(base_url, exc)) from exc
    payload = response.json()
    return [entry.get("id", "") for entry in payload.get("data", []) if entry.get("id")]


def extract(
    *,
    image_data_url: str,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float,
    use_schema: bool = True,
) -> tuple[str, str]:
    """Send the photo to the model. Returns (raw response text, mode used)."""
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        }
    ]
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 2048,
    }

    if use_schema:
        schema_body = dict(body)
        schema_body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "receipt", "strict": True, "schema": RECEIPT_SCHEMA},
        }
        try:
            return _post(base_url, api_key, schema_body, timeout), "json_schema"
        except ExtractionError as exc:
            # A model that cannot be constrained (Gemma often can't) answers 400.
            # Plain prompting still works, so try that before giving up.
            log.warning("Structured output rejected, retrying without a schema: %s", exc)

    return _post(base_url, api_key, body, timeout), "prompt"


def _post(base_url: str, api_key: str, body: dict[str, Any], timeout: float) -> str:
    try:
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise ExtractionError(_connection_hint(base_url, exc)) from exc

    if response.status_code >= 400:
        raise ExtractionError(
            f"LM Studio returned {response.status_code}: {response.text[:400]}"
        )

    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise ExtractionError(f"Unexpected response shape from LM Studio: {exc}") from exc

    if isinstance(content, list):  # some servers return content parts
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not content:
        raise ExtractionError("The model returned an empty response.")
    return content


def _connection_hint(base_url: str, exc: Exception) -> str:
    if isinstance(exc, httpx.ConnectError):
        return (
            f"Could not reach LM Studio at {base_url}. Check that the server is "
            "started (Developer tab -> Start Server) and a vision model is loaded."
        )
    if isinstance(exc, httpx.ReadTimeout):
        return (
            "LM Studio did not answer in time. Large vision models can take "
            "minutes on a photo - raise LMSTUDIO_TIMEOUT or lower MAX_IMAGE_EDGE."
        )
    return f"Could not talk to LM Studio at {base_url}: {exc}"
