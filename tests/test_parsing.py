import pytest

from app import parsing


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("12.50", 12.50),
        ("£12.50", 12.50),
        ("€1.234,56", 1234.56),
        ("1,234.56", 1234.56),
        ("12,50", 12.50),
        ("-3.00", -3.00),
        ("(3.00)", -3.00),
        ("  7 ", 7.0),
        ("12.", 12.0),
        (9.99, 9.99),
        ("", None),
        ("n/a", None),
        (None, None),
    ],
)
def test_parse_amount(raw, expected):
    assert parsing.parse_amount(raw) == expected


@pytest.mark.parametrize(
    "raw, day_first, expected",
    [
        ("2025-03-04", True, "2025-03-04"),
        ("03/04/2025", True, "2025-04-03"),
        ("03/04/2025", False, "2025-03-04"),
        ("25/12/24", True, "2024-12-25"),
        ("13/01/2025", False, "2025-01-13"),  # 13 cannot be a month, so swap
        ("4 July 2025", True, "2025-07-04"),
        ("Jul 4 2025", True, "2025-07-04"),
        ("1st May 2025", True, "2025-05-01"),
        ("nonsense", True, None),
        ("32/01/2025", True, None),
        (None, True, None),
    ],
)
def test_parse_date(raw, day_first, expected):
    assert parsing.parse_date(raw, day_first=day_first) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("14:05", "14:05"), ("2:05 PM", "14:05"), ("12:30 AM", "00:30"),
     ("09:15:44", "09:15"), ("99:99", None), ("", None)],
)
def test_parse_time(raw, expected):
    assert parsing.parse_time(raw) == expected


def test_parse_currency_from_symbol_in_body():
    assert parsing.parse_currency(None, "total £14.20") == "GBP"
    assert parsing.parse_currency("eur") == "EUR"
    assert parsing.parse_currency("$") == "USD"
    assert parsing.parse_currency(None, "nothing here") is None


class TestJsonExtraction:
    def test_bare_object(self):
        assert parsing.extract_json_object('{"total": 5}') == {"total": 5}

    def test_fenced(self):
        text = 'Sure!\n```json\n{"total": 5}\n```\nHope that helps.'
        assert parsing.extract_json_object(text) == {"total": 5}

    def test_surrounded_by_prose(self):
        text = 'Here is the receipt: {"merchant": "Co-op", "total": 5} - let me know.'
        assert parsing.extract_json_object(text)["merchant"] == "Co-op"

    def test_braces_inside_strings_do_not_confuse_the_scanner(self):
        text = '{"notes": "a } brace", "total": 1}'
        assert parsing.extract_json_object(text)["notes"] == "a } brace"

    def test_no_json_raises(self):
        with pytest.raises(ValueError):
            parsing.extract_json_object("I cannot read this receipt, sorry.")


def test_normalise_extraction_maps_alternative_keys():
    payload = {
        "store": "Sainsbury's",
        "purchase_date": "03/04/2025",
        "purchase_time": "5:42 pm",
        "total_amount": "£23,45",
        "vat": "3.91",
        "items": [
            {"name": "Milk 2L", "qty": "2", "price": "1.25", "amount": "2.50"},
            "Loose banana",
            {"description": None, "quantity": None},
        ],
    }
    result = parsing.normalise_extraction(payload, day_first=True)

    assert result["fields"]["merchant"] == "Sainsbury's"
    assert result["fields"]["purchased_on"] == "2025-04-03"
    assert result["fields"]["purchased_at"] == "17:42"
    assert result["fields"]["total"] == 23.45
    assert result["fields"]["tax"] == 3.91
    assert result["fields"]["currency"] == "GBP"  # inferred from the £ in the payload
    assert len(result["line_items"]) == 2
    assert result["line_items"][0] == {
        "description": "Milk 2L", "quantity": 2.0, "unit_price": 1.25, "line_total": 2.50,
    }
    assert result["line_items"][1]["description"] == "Loose banana"


def test_totals_mismatch_flags_only_real_gaps():
    items = [{"line_total": 10.0}, {"line_total": 5.0}]
    assert parsing.totals_mismatch({"total": 15.0, "tax": None, "tip": None}, items) is None
    assert parsing.totals_mismatch({"total": 18.0, "tax": 3.0, "tip": None}, items) is None
    assert parsing.totals_mismatch({"total": 99.0, "tax": None, "tip": None}, items) is not None
    assert parsing.totals_mismatch({"total": None}, items) is None
    assert parsing.totals_mismatch({"total": 15.0}, []) is None
