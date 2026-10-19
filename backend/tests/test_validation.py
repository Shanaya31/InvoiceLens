from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from app.schemas.document import DocumentRecord, ExtractedFields
from app.services.validation import (
    apply_validation,
    build_field_review,
    is_valid_gstin_format,
    validate_fields,
)

TOL = Decimal("0.01")
TODAY = date(2026, 10, 13)


def fields(**overrides) -> ExtractedFields:
    base = {
        "document_type": "invoice",
        "vendor_name": "Sample Office Supplies",
        "vendor_gstin": "27AAPFU0939F1ZV",
        "invoice_number": "INV-1042",
        "invoice_date": "2026-10-12",
        "subtotal": "10000.00",
        "cgst_amount": "900.00",
        "sgst_amount": "900.00",
        "total_tax_amount": "1800.00",
        "total_amount": "11800.00",

    }
    base.update(overrides)
    return ExtractedFields(**base)


def run(**overrides):
    return validate_fields(fields(**overrides), tolerance=TOL, today=TODAY)


def test_clean_invoice_has_no_flags_and_valid_arithmetic():
    outcome = run()
    assert outcome.flags == []
    assert outcome.arithmetic_valid is True


def test_total_mismatch_is_flagged():
    outcome = run(total_amount="12000.00")
    assert "total_mismatch" in outcome.flags
    assert outcome.arithmetic_valid is False


def test_tolerance_is_configurable():
    off_by_fifty_paise = {"total_amount": "11800.50"}
    assert "total_mismatch" in run(**off_by_fifty_paise).flags
    loose = validate_fields(fields(**off_by_fifty_paise), tolerance=Decimal("1.00"), today=TODAY)
    assert loose.flags == []
    assert loose.arithmetic_valid is True


def test_discount_is_subtracted_before_tax():
    outcome = run(discount_amount="1000.00", subtotal="11000.00")
    assert outcome.arithmetic_valid is True


def test_igst_only_invoice_is_valid():
    outcome = run(cgst_amount=None, sgst_amount=None, igst_amount="1800.00")
    assert outcome.flags == []
    assert outcome.arithmetic_valid is True


def test_mixed_cgst_and_igst_is_flagged():
    outcome = run(igst_amount="1800.00", total_tax_amount="3600.00", total_amount="13600.00")
    assert "mixed_gst_tax_types" in outcome.flags


def test_unequal_cgst_sgst_is_flagged():
    outcome = run(cgst_amount="1000.00", sgst_amount="800.00")
    assert "cgst_sgst_unequal" in outcome.flags


def test_tax_total_must_match_components():
    outcome = run(total_tax_amount="1500.00")
    assert "tax_total_mismatch" in outcome.flags
    assert outcome.arithmetic_valid is False


def test_missing_tax_component_does_not_validate_incomplete_tax_sum():
    outcome = run(
        cgst_amount="900.00",
        sgst_amount=None,
        total_tax_amount=None,
        total_amount="10900.00",
    )
    assert outcome.arithmetic_valid is None


def test_reported_total_tax_can_validate_total_when_components_are_missing():
    outcome = run(
        cgst_amount=None,
        sgst_amount=None,
        total_tax_amount="1800.00",
        total_amount="11800.00",
    )
    assert outcome.arithmetic_valid is True


def test_arithmetic_is_not_checked_without_enough_data():
    outcome = run(subtotal=None, cgst_amount=None, sgst_amount=None, total_tax_amount=None)
    assert outcome.arithmetic_valid is None


def test_decimal_math_has_no_float_drift():
    outcome = run(subtotal="0.10", cgst_amount="0.01", sgst_amount="0.01", total_tax_amount="0.02", total_amount="0.12")
    assert outcome.arithmetic_valid is True


def test_missing_required_fields_are_flagged():
    outcome = run(vendor_name=None, invoice_date=None, total_amount=None, invoice_number=None)
    for flag in ("vendor_name_missing", "invoice_date_missing", "total_amount_missing", "invoice_number_missing"):
        assert flag in outcome.flags


def test_future_and_non_positive_values_are_flagged():
    assert "invoice_date_in_future" in run(invoice_date="2999-01-01").flags
    assert "total_not_positive" in run(total_amount="0.00", subtotal=None, total_tax_amount=None, cgst_amount=None, sgst_amount=None).flags


def test_missing_gstin_is_only_noted_when_tax_is_charged():
    assert "vendor_gstin_missing" in run(vendor_gstin=None).flags
    no_tax = run(vendor_gstin=None, cgst_amount=None, sgst_amount=None, igst_amount=None,
                 total_tax_amount=None, subtotal="500.00", total_amount="500.00")
    assert "vendor_gstin_missing" not in no_tax.flags


def test_receipts_do_not_need_gstin_or_invoice_number():
    outcome = run(document_type="receipt", vendor_gstin=None, invoice_number=None)
    assert "vendor_gstin_missing" not in outcome.flags
    assert "invoice_number_missing" not in outcome.flags


@pytest.mark.parametrize("value, ok", [
    ("27AAPFU0939F1ZV", True),
    ("27aapfu0939f1zv", True),
    ("27AAPFU0939F1Z", False),
    ("AAAAAAAAAAAAAAA", False),
    ("27AAPFU0939F1XV", False),  # 14th character must be Z
    ("", False),
])
def test_gstin_format_check(value, ok):
    assert is_valid_gstin_format(value) is ok


def test_invalid_gstin_format_is_flagged_but_valid_format_is_not_called_registered():
    assert "vendor_gstin_invalid_format" in run(vendor_gstin="12345").flags
    assert run().flags == []  # a well-formed number simply passes the *format* check


def test_field_review_marks_the_relevant_key_fields():
    review = build_field_review(["vendor_gstin_missing", "total_mismatch", "invoice_date_ambiguous"])
    assert review["vendor_gstin"] is True
    assert review["total_amount"] is True
    assert review["invoice_date"] is True  # extraction flag prefixed with the field name
    assert review["vendor_name"] is False


def test_field_review_marks_total_tax_when_component_sum_mismatches():
    review = build_field_review(["tax_total_mismatch"])

    assert review["total_tax_amount"] is True
    assert review["total_amount"] is False
    assert review["vendor_name"] is False


def make_record(**overrides) -> DocumentRecord:
    base = fields(**overrides).model_dump()
    return DocumentRecord(**base, document_id="doc_test", filename="x.png", extraction_method="gemma_vision")


def test_apply_validation_always_requires_review_until_reviewed():
    record = apply_validation(make_record(), TOL, TODAY)
    assert record.review_flags == []
    assert record.validation.requires_human_review is True
    reviewed = apply_validation(record.model_copy(update={"reviewed": True}), TOL, TODAY)
    assert reviewed.validation.requires_human_review is False


def test_corrections_clear_stale_extraction_flags():
    record = make_record(invoice_date=None).model_copy(update={"extraction_flags": ["invoice_date_ambiguous", "mock_extraction"]})
    flagged = apply_validation(record, TOL, TODAY)
    assert "invoice_date_ambiguous" in flagged.review_flags
    assert flagged.field_review["invoice_date"] is True

    fixed = flagged.model_copy(update={"invoice_date": "2026-10-12", "corrected_fields": ["invoice_date"]})
    revalidated = apply_validation(fixed, TOL, TODAY)
    assert "invoice_date_ambiguous" not in revalidated.review_flags
    assert "invoice_date_missing" not in revalidated.review_flags
    assert "mock_extraction" in revalidated.review_flags  # unrelated flags are kept
    assert revalidated.field_review["invoice_date"] is False

def test_doubled_reported_tax_flags_tax_and_total_mismatches():
    outcome = run(
        subtotal="2500.00",
        cgst_amount="225.00",
        sgst_amount="225.00",
        total_tax_amount="900.00",
        total_amount="2950.00",
    )

    assert "tax_total_mismatch" in outcome.flags
    assert "total_mismatch" in outcome.flags
    assert outcome.arithmetic_valid is False
