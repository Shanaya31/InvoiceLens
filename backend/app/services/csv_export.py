"""CSV export of the (possibly corrected) records.

- Text cells are neutralised against spreadsheet formula injection (a leading ``= + - @`` gets an
  apostrophe prefix). Amount columns are written as plain decimal strings.
- Invoice numbers are written verbatim, so leading zeros survive in the file itself. Excel may
  still drop them when double-clicking a CSV; import the column as Text to keep them.
"""
from __future__ import annotations

import csv
import io
from collections.abc import Iterable

from app.schemas.document import DocumentRecord

CSV_COLUMNS = [
    "document_id",
    "filename",
    "document_type",
    "vendor_name",
    "vendor_gstin",
    "invoice_number",
    "invoice_date",
    "currency",
    "subtotal",
    "discount_amount",
    "cgst_amount",
    "sgst_amount",
    "igst_amount",
    "other_tax_amount",
    "total_tax_amount",
    "total_amount",
    "expense_category",
    "extraction_method",
    "reviewed",
    "requires_human_review",
    "review_flags",
]

_TEXT_COLUMNS = {
    "filename",
    "vendor_name",
    "vendor_gstin",
    "invoice_number",
    "expense_category",
    "review_flags",
}
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _text_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def _row(record: DocumentRecord) -> list[str]:
    values = {
        **record.model_dump(),
        "requires_human_review": record.validation.requires_human_review,
        "review_flags": ";".join(record.review_flags),
    }
    row: list[str] = []
    for column in CSV_COLUMNS:
        value = values.get(column)
        if column in _TEXT_COLUMNS:
            row.append(_text_cell(value))
        elif isinstance(value, bool):
            row.append("true" if value else "false")
        else:
            row.append("" if value is None else str(value))
    return row


def records_to_csv(records: Iterable[DocumentRecord], reviewed_only: bool = False) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for record in records:
        if reviewed_only and not record.reviewed:
            continue
        writer.writerow(_row(record))
    return buffer.getvalue()
