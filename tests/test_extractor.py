import httpx
import pytest

from app import extractor


def _completion(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def test_falls_back_to_plain_prompting_when_schema_is_rejected(monkeypatch):
    """Gemma and friends reject json_schema mode; we must not give up there."""
    attempts = []

    def fake_post(url, headers=None, json=None, timeout=None):
        attempts.append(json)
        if "response_format" in json:
            return httpx.Response(400, text="'response_format.json_schema' is not supported")
        return httpx.Response(200, json=_completion('{"total": 1}'))

    monkeypatch.setattr(httpx, "post", fake_post)

    content, mode = extractor.extract(
        image_data_url="data:image/jpeg;base64,xxx",
        base_url="http://127.0.0.1:1234/v1",
        api_key="lm-studio",
        model="google/gemma-4-12b",
        timeout=5,
    )
    assert content == '{"total": 1}'
    assert mode == "prompt"
    assert len(attempts) == 2
    assert "response_format" in attempts[0] and "response_format" not in attempts[1]


def test_prompt_and_image_share_one_user_turn(monkeypatch):
    """Gemma's template has no system role, so everything rides in the user turn."""
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(json)
        return httpx.Response(200, json=_completion("{}"))

    monkeypatch.setattr(httpx, "post", fake_post)
    extractor.extract(
        image_data_url="data:image/jpeg;base64,xxx",
        base_url="http://127.0.0.1:1234/v1",
        api_key="lm-studio",
        model="m",
        timeout=5,
        use_schema=False,
    )

    assert [m["role"] for m in captured["messages"]] == ["user"]
    parts = {part["type"] for part in captured["messages"][0]["content"]}
    assert parts == {"text", "image_url"}
    assert captured["temperature"] == 0


def test_connection_refused_gives_an_actionable_message(monkeypatch):
    def fake_post(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(extractor.ExtractionError) as exc:
        extractor.extract(
            image_data_url="x",
            base_url="http://127.0.0.1:1234/v1",
            api_key="k",
            model="m",
            timeout=5,
            use_schema=False,
        )
    assert "Start Server" in str(exc.value)


def test_content_returned_as_parts_is_joined(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(
            200, json={"choices": [{"message": {"content": [{"type": "text", "text": '{"a": 1}'}]}}]}
        ),
    )
    content, _ = extractor.extract(
        image_data_url="x", base_url="u", api_key="k", model="m", timeout=5, use_schema=False
    )
    assert content == '{"a": 1}'


def test_list_models(monkeypatch):
    request = httpx.Request("GET", "http://127.0.0.1:1234/v1/models")
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **k: httpx.Response(
            200, json={"data": [{"id": "google/gemma-4-12b"}, {"id": "x"}]}, request=request
        ),
    )
    assert extractor.list_models("u", "k") == ["google/gemma-4-12b", "x"]
