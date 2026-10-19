"""Deterministic validation. Runs in Python so we never trust model-generated validation.

All money maths uses ``Decimal``. GSTIN checks are format-only: a well-formed GSTIN is
not proof that the number is registered.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.schemas.document import (
    KEY_FIELDS,
    DocumentRecord,
    ExtractedFields,
    ValidationResult,
)

GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

# Validation flag -> the key field it should highlight in the review form.
FLAG_TO_FIELD = {
    "vendor_name_missing": "vendor_name",
    "vendor_gstin_missing": "vendor_gstin",
    "vendor_gstin_invalid_format": "vendor_gstin",
    "invoice_number_missing": "invoice_number",
    "invoice_date_missing": "invoice_date",
    "invoice_date_in_future": "invoice_date",
    "total_amount_missing": "total_amount",
    "total_mismatch": "total_amount",
    "total_not_positive": "total_amount",
    "tax_total_mismatch": "total_tax_amount",
}


def is_valid_gstin_format(value: str) -> bool:
    return bool(GSTIN_RE.match(value.strip().upper()))


def _dec(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


@dataclass
class ValidationOutcome:
    flags: list[str]
    arithmetic_valid: bool | None


def validate_fields(
    fields: ExtractedFields,
    *,
    tolerance: Decimal,
    today: date | None = None,
) -> ValidationOutcome:
    today = today or date.today()
    flags: list[str] = []

    subtotal = _dec(fields.subtotal)
    discount = _dec(fields.discount_amount)
    cgst, sgst, igst = _dec(fields.cgst_amount), _dec(fields.sgst_amount), _dec(fields.igst_amount)
    other = _dec(fields.other_tax_amount)
    total_tax = _dec(fields.total_tax_amount)
    total = _dec(fields.total_amount)

    # --- required fields -------------------------------------------------
    if fields.document_type == "unknown":
        flags.append("document_type_unknown")
    if not fields.vendor_name:
        flags.append("vendor_name_missing")
    if not fields.invoice_date:
        flags.append("invoice_date_missing")
    elif date.fromisoformat(fields.invoice_date) > today:
        flags.append("invoice_date_in_future")
    if total is None:
        flags.append("total_amount_missing")
    elif total <= 0:
        flags.append("total_not_positive")
    if fields.document_type == "invoice" and not fields.invoice_number:
        flags.append("invoice_number_missing")  # receipts often have no invoice number

    # --- GSTIN: format only; missing is only noteworthy when GST is charged ----
    gst_charged = any(v is not None and v > 0 for v in (cgst, sgst, igst))
    if fields.vendor_gstin:
        if not is_valid_gstin_format(fields.vendor_gstin):
            flags.append("vendor_gstin_invalid_format")
    elif fields.document_type == "invoice" and gst_charged:
        flags.append("vendor_gstin_missing")

    # --- tax structure: intra-state (CGST+SGST) vs inter-state (IGST) ----------
    if igst is not None and igst > 0 and any(v is not None and v > 0 for v in (cgst, sgst)):
        flags.append("mixed_gst_tax_types")
    if cgst is not None and sgst is not None and abs(cgst - sgst) > tolerance:
        flags.append("cgst_sgst_unequal")

        # --- arithmetic ------------------------------------------------------------
    checks: list[bool] = []

    # A component breakdown is complete when it represents either:
    # - intra-state GST: both CGST and SGST are available, or
    # - inter-state GST: IGST is available and CGST/SGST are absent or zero.
    #
    # "Other tax" is optional and is included when supplied.
    intra_state_complete = (
        cgst is not None
        and sgst is not None
        and (igst is None or igst == 0)
    )
    inter_state_complete = (
        igst is not None
        and (cgst is None or cgst == 0)
        and (sgst is None or sgst == 0)
    )
    component_breakdown_complete = (
        intra_state_complete or inter_state_complete
    )

    parts = [
        value
        for value in (cgst, sgst, igst, other)
        if value is not None
    ]
    component_sum = (
        sum(parts, Decimal(0))
        if component_breakdown_complete
        else None
    )

    # Compare reported tax against components only when the breakdown is complete.
    if total_tax is not None and component_sum is not None:
        ok = abs(total_tax - component_sum) <= tolerance
        checks.append(ok)
        if not ok:
            flags.append("tax_total_mismatch")

    # Prefer reported total tax. Otherwise, use components only if complete.
    tax: Decimal | None = (
        total_tax if total_tax is not None else component_sum
    )

    # Never assume missing tax information means zero tax.
    if subtotal is not None and total is not None and tax is not None:
        expected = subtotal - (discount or Decimal(0)) + tax
        ok = abs(expected - total) <= tolerance
        checks.append(ok)
        if not ok:
            flags.append("total_mismatch")

    arithmetic_valid = all(checks) if checks else None

    return ValidationOutcome(flags=flags, arithmetic_valid=arithmetic_valid)


def build_field_review(flags: list[str]) -> dict[str, bool]:
    review = {key: False for key in KEY_FIELDS}
    for flag in flags:
        field = FLAG_TO_FIELD.get(flag)
        if field is None:
            field = next((k for k in KEY_FIELDS if flag.startswith(k + "_")), None)
        if field:
            review[field] = True
    return review


def apply_validation(
    record: DocumentRecord,
    tolerance: Decimal,
    today: date | None = None,
) -> DocumentRecord:
    """Return a copy of ``record`` with flags and validation recomputed from its current values.

    Extraction-stage flags about a field (``invoice_date_ambiguous``...) are dropped once the
    user has corrected that field, so corrections are preserved and stale warnings disappear.
    """
    corrected = set(record.corrected_fields)
    carried = [
        flag
        for flag in record.extraction_flags
        if not any(flag.startswith(field + "_") for field in corrected)
    ]
    outcome = validate_fields(record, tolerance=tolerance, today=today)
    flags = list(dict.fromkeys([*carried, *outcome.flags]))
    return record.model_copy(
        update={
            "review_flags": flags,
            "field_review": build_field_review(flags),
            "validation": ValidationResult(
                schema_valid=True,
                arithmetic_valid=outcome.arithmetic_valid,
                requires_human_review=not record.reviewed,
            ),
        }
    )
