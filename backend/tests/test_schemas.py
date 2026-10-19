from __future__ import annotations

from decimal import Decimal

import pytest
from app.schemas.document import (
    DocumentUpdate,
    ExtractedFields,
    parse_iso_date,
    parse_money,
)
from pydantic import ValidationError


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("11800", "11800.00"),
        ("11,800.5", "11800.50"),
        ("₹ 1,18,000.00", "118000.00"),
        ("Rs. 250", "250.00"),
        ("INR 99.999", "100.00"),
        (500, "500.00"),
        (Decimal("12.345"), "12.35"),
        (11800.5, "11800.50"),
        ("-100", "-100.00"),
        (None, None),
        ("", None),
        ("null", None),
        ("N/A", None),
    ],
)
def test_parse_money(raw, expected):
    assert parse_money(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "12.3.4", "NaN", "Infinity", True, ["1"], {"a": 1}])
def test_parse_money_rejects_unreadable_values(raw):
    with pytest.raises(ValueError):
        parse_money(raw)


def test_float_money_does_not_leak_binary_noise():
    assert parse_money(0.1 + 0.2) == "0.30"


@pytest.mark.parametrize(
    "raw, expected",
    [("2026-10-12", "2026-10-12"), (" 2026-01-05 ", "2026-01-05"), (None, None), ("", None), ("null", None)],
)
def test_parse_iso_date_accepts_iso_only(raw, expected):
    assert parse_iso_date(raw) == expected


@pytest.mark.parametrize("raw", ["12/10/2026", "03-04-2026", "12 Oct 2026", "2026-13-01", "2026-02-30", 20261012])
def test_parse_iso_date_rejects_ambiguous_or_invalid(raw):
    with pytest.raises(ValueError):
        parse_iso_date(raw)


def test_all_fields_are_nullable_with_sensible_defaults():
    fields = ExtractedFields()
    assert fields.document_type == "unknown"
    assert fields.currency == "INR"
    for name in ("vendor_name", "vendor_gstin", "invoice_number", "invoice_date", "subtotal", "total_amount"):
        assert getattr(fields, name) is None


def test_document_type_must_be_known():
    assert ExtractedFields(document_type=" Invoice ").document_type == "invoice"
    with pytest.raises(ValidationError):
        ExtractedFields(document_type="bill")


def test_gstin_is_normalised_but_not_judged_by_the_schema():
    assert ExtractedFields(vendor_gstin="27 aapfu0939f1zv").vendor_gstin == "27AAPFU0939F1ZV"
    # Format problems are flagged by validation, not rejected, so the user can still fix them.
    assert ExtractedFields(vendor_gstin="NOT-A-GSTIN").vendor_gstin == "NOT-A-GSTIN"


def test_null_tokens_become_none_in_text_fields():
    assert ExtractedFields(vendor_name="null", invoice_number="N/A").vendor_name is None
    assert ExtractedFields(invoice_number=1042).invoice_number == "1042"


def test_money_fields_are_strings_never_floats():
    fields = ExtractedFields(total_amount=11800.5, subtotal=Decimal(10000))
    assert fields.total_amount == "11800.50"
    assert fields.subtotal == "10000.00"
    assert isinstance(fields.total_amount, str)


def test_update_distinguishes_unset_from_explicit_null():
    untouched = DocumentUpdate()
    assert untouched.model_dump(exclude_unset=True) == {}
    cleared = DocumentUpdate(vendor_gstin=None)
    assert cleared.model_dump(exclude_unset=True) == {"vendor_gstin": None}


def test_update_cannot_clear_document_type_or_currency():
    with pytest.raises(ValidationError):
        DocumentUpdate(document_type=None)
    with pytest.raises(ValidationError):
        DocumentUpdate(currency=None)
    assert DocumentUpdate(currency="inr").currency == "INR"
