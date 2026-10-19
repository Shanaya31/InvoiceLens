"""CSV escaping and the spreadsheet-safety rules."""
from __future__ import annotations

import csv
import io
from decimal import Decimal

from app.schemas.document import DocumentRecord
from app.services.csv_export import CSV_COLUMNS, records_to_csv
from app.services.validation import apply_validation


def record(**overrides) -> DocumentRecord:
    base = {
        "document_id": "doc_test",
        "filename": "invoice.png",
        "extraction_method": "gemma_vision",
        "document_type": "invoice",
        "vendor_name": "Sample Office Supplies",
        "invoice_number": "INV-1042",
        "invoice_date": "2025-06-15",
        "subtotal": "10000.00",
        "total_amount": "10000.00",
    }
    return apply_validation(DocumentRecord(**{**base, **overrides}), Decimal("1.00"))


def parse(text: str):
    return list(csv.DictReader(io.StringIO(text)))


def test_header_matches_columns_and_empty_export_has_only_header():
    text = records_to_csv([])
    assert text.strip().split(",") == CSV_COLUMNS
    assert parse(text) == []


def test_commas_quotes_and_newlines_round_trip():
    tricky = 'Foo, "Bar" & Sons\nPvt. Ltd.'
    rows = parse(records_to_csv([record(vendor_name=tricky)]))
    assert rows[0]["vendor_name"] == tricky


def test_leading_zeros_in_invoice_numbers_are_preserved_in_the_file():
    text = records_to_csv([record(invoice_number="000123")])
    assert parse(text)[0]["invoice_number"] == "000123"
    assert ",000123," in text

def test_formula_injection_is_neutralised():
    payloads = (
        '=HYPERLINK("http://evil","x")',
        "+1+1",
        "-2+3",
        "@SUM(A1)",
    )

    for payload in payloads:
        rows = parse(records_to_csv([record(vendor_name=payload)]))
        assert rows[0]["vendor_name"] == "'" + payload

def test_tab_and_carriage_return_prefixes_are_neutralised():
    for payload in ("\t=SUM(A1)", "\r=SUM(A1)"):
        text = records_to_csv([record(vendor_name=payload)])
        assert "vendor_name" in text
        assert "'=SUM(A1)" in text

def test_missing_values_are_empty_cells_and_amounts_are_plain_decimals():
    rows = parse(records_to_csv([record(discount_amount=None, cgst_amount="900")]))
    assert rows[0]["discount_amount"] == ""
    assert rows[0]["cgst_amount"] == "900.00"
    assert rows[0]["total_amount"] == "10000.00"


def test_review_flags_and_booleans():
    rows = parse(records_to_csv([record(reviewed=True)]))
    assert rows[0]["reviewed"] == "true"
    assert rows[0]["requires_human_review"] == "false"
    flagged = parse(records_to_csv([record(vendor_name=None)]))
    assert "vendor_name_missing" in flagged[0]["review_flags"]
    assert flagged[0]["reviewed"] == "false"


def test_reviewed_only_filter():
    rows = parse(records_to_csv([record(document_id="a"), record(document_id="b", reviewed=True)], reviewed_only=True))
    assert [row["document_id"] for row in rows] == ["b"]


def test_csv_endpoint_includes_utf8_bom(client):
    response = client.get(
        "/api/export.csv",
        headers={"X-Session-ID": "csv-bom-test"},
    )

    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert "text/csv" in response.headers["content-type"]
