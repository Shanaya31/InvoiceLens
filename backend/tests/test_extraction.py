"""Extraction workflow: Gemma vision -> OCR text -> one retry. No Ollama or Tesseract needed."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest
from app.services.extraction import (
    ExtractionFailedError,
    MalformedModelResponse,
    _recover_vendor_gstin_from_ocr,
    build_record,
    extract_document,
    is_usable,
    parse_model_json,
    sanitize_fields,
)

from app.services.gemma import (
    MockModelClient,
    ModelCapabilityError,
    ModelResponseError,
    ModelTimeoutError,
    ModelUnavailableError,
)
from app.services.ocr import OCRError, OCRUnavailableError
from fakes import GOOD_INVOICE, FakeClient, FakeOCR

IMAGES = [b"fake-image"]
PARTIAL = {**GOOD_INVOICE, "total_amount": None, "subtotal": None}  # missing a required field

def test_ocr_recovers_gstin_near_vendor_name():
    text = (
        "Sample Office Supplies\n"
        "GSTIN: 27AAPFU0939F1ZV\n"
        "Invoice INV-1042"
    )
    assert _recover_vendor_gstin_from_ocr(
        text, "Sample Office Supplies"
    ) == "27AAPFU0939F1ZV"


def test_ocr_does_not_recover_buyer_gstin():
    text = (
        "Sample Office Supplies\n"
        "Buyer GSTIN: 29AAGCT1234F1ZV\n"
        "Invoice INV-1042"
    )
    assert _recover_vendor_gstin_from_ocr(
        text, "Sample Office Supplies"
    ) is None


def test_ocr_does_not_choose_between_multiple_gstins_near_vendor():
    text = (
        "Sample Office Supplies\n"
        "GSTIN: 27AAPFU0939F1ZV\n"
        "Alternate GSTIN: 29AAGCT1234F1ZV"
    )
    assert _recover_vendor_gstin_from_ocr(
        text, "Sample Office Supplies"
    ) is None

def test_ocr_recovers_seller_gstin_when_buyer_has_a_separate_gstin():
    text = (
        "TAX INVOICE\n"
        "Seller: Zenith Electronics Pvt. Ltd.\n"
        "GSTIN: 27AAPFU0939F1ZV\n"
        "Invoice No: ZE-2026-0789\n"
        "Bill To: Customer Example\n"
        "Buyer GSTIN: 29AAGCT1234F1ZV\n"
    )

    assert _recover_vendor_gstin_from_ocr(
        text, "Zenith Electronics Pvt. Ltd."
    ) == "27AAPFU0939F1ZV"


def test_ocr_does_not_recover_malformed_gstin():
    text = (
        "Sample Office Supplies\n"
        "GSTIN: 27AAPFU0939F1X"
    )
    assert _recover_vendor_gstin_from_ocr(
        text, "Sample Office Supplies"
    ) is None

def _forbidden_ocr(images):
    raise AssertionError("OCR must not run when the first attempt is usable")


# ---- parsing the model's answer ---------------------------------------------------------------
def test_parse_plain_json():
    assert parse_model_json('{"vendor_name": "A"}') == {"vendor_name": "A"}


def test_parse_json_in_code_fence():
    assert parse_model_json('```json\n{"vendor_name": "A"}\n```') == {"vendor_name": "A"}


def test_parse_json_surrounded_by_prose():
    assert parse_model_json('Here you go: {"vendor_name": "A"} Hope that helps!') == {"vendor_name": "A"}


@pytest.mark.parametrize("text", ["", "no json here", '{"unterminated": ', "[1, 2, 3]", '"just a string"'])
def test_parse_rejects_non_objects(text):
    with pytest.raises(MalformedModelResponse):
        parse_model_json(text)


def test_json_numbers_never_become_binary_floats():
    raw = parse_model_json('{"total_amount": 11800.5, "subtotal": 0.10}')
    fields, flags = sanitize_fields(raw)
    assert fields.total_amount == "11800.50"
    assert fields.subtotal == "0.10"
    assert flags == []


# ---- field sanitising: bad values become None + a flag, never a guess ---------------------------
def test_empty_answer_stays_empty_nothing_is_invented():
    fields, flags = sanitize_fields({})
    assert fields.vendor_name is None and fields.total_amount is None and fields.invoice_date is None
    assert fields.document_type == "unknown"
    assert flags == []


def test_unreadable_amount_is_nulled_and_flagged():
    fields, flags = sanitize_fields({"total_amount": "eleven thousand"})
    assert fields.total_amount is None
    assert "total_amount_unreadable" in flags


def test_ambiguous_date_is_nulled_and_flagged():
    fields, flags = sanitize_fields({"invoice_date": "03/04/2026"})
    assert fields.invoice_date is None
    assert "invoice_date_ambiguous" in flags


def test_one_bad_field_does_not_sink_the_rest():
    fields, flags = sanitize_fields({**GOOD_INVOICE, "igst_amount": "n/a?!", "document_type": "banana"})
    assert fields.vendor_name == "Sample Office Supplies"
    assert fields.total_amount == "11800.00"
    assert fields.document_type == "unknown"
    assert set(flags) == {"igst_amount_unreadable", "document_type_unreadable"}


def test_is_usable_requires_key_fields():
    good, _ = sanitize_fields(GOOD_INVOICE)
    assert is_usable(good)
    no_number, _ = sanitize_fields({**GOOD_INVOICE, "invoice_number": None})
    assert not is_usable(no_number)  # an invoice needs a number
    receipt, _ = sanitize_fields({**GOOD_INVOICE, "document_type": "receipt", "invoice_number": None})
    assert is_usable(receipt)  # a receipt does not
    no_total, _ = sanitize_fields(PARTIAL)
    assert not is_usable(no_total)


# ---- workflow ------------------------------------------------------------------------------------
def test_usable_first_attempt_skips_ocr():
    client = FakeClient([GOOD_INVOICE])
    result = extract_document(IMAGES, client, ocr_fn=_forbidden_ocr)
    assert result.method == "gemma_vision"
    assert result.ocr_attempted is False
    assert client.calls == [(1, None)]

def test_usable_result_recovers_missing_gstin_from_ocr_without_second_model_call():
    client = FakeClient([{**GOOD_INVOICE, "vendor_gstin": None}])
    ocr_text = (
        "Sample Office Supplies\n"
        "GSTIN: 27AAPFU0939F1ZV\n"
        "Invoice INV-1042"
    )

    result = extract_document(
        IMAGES,
        client,
        ocr_fn=lambda images: ocr_text,
    )

    assert result.fields.vendor_gstin == "27AAPFU0939F1ZV"
    assert result.method == "gemma_vision"
    assert result.ocr_attempted is True
    assert "vendor_gstin_recovered_from_ocr" in result.flags
    assert client.calls == [(1, None)]

class VerifyingFakeClient(FakeClient):
    def __init__(self, responses, verification_response):
        super().__init__(responses)
        self.verification_response = verification_response
        self.verification_calls = []

    def verify_vendor_gstin(self, images, vendor_name):
        self.verification_calls.append((images, vendor_name))
        if isinstance(self.verification_response, Exception):
            raise self.verification_response
        return json.dumps(self.verification_response)


def test_matching_gstin_verification_retains_gstin():
    client = VerifyingFakeClient(
        [GOOD_INVOICE],
        {"vendor_gstin": GOOD_INVOICE["vendor_gstin"]},
    )

    result = extract_document(IMAGES, client, ocr_fn=_forbidden_ocr)

    assert result.fields.vendor_gstin == GOOD_INVOICE["vendor_gstin"]
    assert result.fields.total_amount == "11800.00"
    assert result.flags == []
    assert len(client.verification_calls) == 0


def test_mismatched_gstin_verification_clears_gstin():
    client = VerifyingFakeClient(
        [GOOD_INVOICE],
        {"vendor_gstin": "29AAGCT1234F1ZV"},
    )

    result = extract_document(IMAGES, client, ocr_fn=_forbidden_ocr)

    assert result.fields.vendor_gstin == GOOD_INVOICE["vendor_gstin"]
    assert result.fields.vendor_name == GOOD_INVOICE["vendor_name"]
    assert result.fields.total_amount == "11800.00"


def test_unreadable_gstin_verification_clears_gstin():
    client = VerifyingFakeClient(
        [GOOD_INVOICE],
        {"vendor_gstin": None},
    )

    result = extract_document(IMAGES, client, ocr_fn=_forbidden_ocr)

    assert result.fields.vendor_gstin == GOOD_INVOICE["vendor_gstin"]
    assert result.fields.total_amount == "11800.00"


def test_gstin_verification_error_preserves_other_fields():
    client = VerifyingFakeClient(
        [GOOD_INVOICE],
        ModelResponseError("verification failed"),
    )

    result = extract_document(IMAGES, client, ocr_fn=_forbidden_ocr)

    assert result.fields.vendor_gstin == GOOD_INVOICE["vendor_gstin"]
    assert result.fields.vendor_name == GOOD_INVOICE["vendor_name"]
    assert result.fields.total_amount == "11800.00"


def test_existing_fake_client_skips_gstin_verification():
    client = FakeClient([GOOD_INVOICE])

    result = extract_document(IMAGES, client, ocr_fn=_forbidden_ocr)

    assert result.fields.vendor_gstin == GOOD_INVOICE["vendor_gstin"]
    assert client.calls == [(1, None)]


def test_malformed_json_triggers_one_ocr_assisted_retry():
    client = FakeClient(["this is not json", GOOD_INVOICE])
    ocr = FakeOCR(text="Sample Office Supplies INV-1042")
    result = extract_document(IMAGES, client, ocr_fn=ocr)
    assert result.method == "ocr_gemma"
    assert result.ocr_attempted is True
    assert "model_response_invalid" in result.flags
    assert client.calls == [(1, None), (0, "Sample Office Supplies INV-1042")]
    assert ocr.calls == 1


def test_incomplete_first_attempt_is_recovered_by_ocr():
    client = FakeClient([PARTIAL, GOOD_INVOICE])
    result = extract_document(IMAGES, client, ocr_fn=FakeOCR())
    assert result.method == "ocr_gemma"
    assert result.fields.total_amount == "11800.00"


def test_timeout_then_ocr_recovery():
    client = FakeClient([ModelTimeoutError("slow"), GOOD_INVOICE])
    result = extract_document(IMAGES, client, ocr_fn=FakeOCR())
    assert result.method == "ocr_gemma"
    assert "model_timeout" in result.flags


def test_retry_happens_at_most_once():
    client = FakeClient([PARTIAL, PARTIAL])  # a third call would raise AssertionError
    result = extract_document(IMAGES, client, ocr_fn=FakeOCR())
    assert len(client.calls) == 2
    assert result.fields.total_amount is None  # still unknown: not guessed


def test_worse_ocr_attempt_does_not_replace_better_first_attempt():
    sparse = {"vendor_name": "Sample Office Supplies"}
    client = FakeClient([PARTIAL, sparse])
    result = extract_document(IMAGES, client, ocr_fn=FakeOCR())
    assert result.method == "gemma_vision"
    assert result.fields.invoice_date == PARTIAL["invoice_date"]


def test_ocr_unavailable_keeps_partial_result_and_flags_it():
    client = FakeClient([PARTIAL])
    ocr = FakeOCR(error=OCRUnavailableError("Tesseract is not installed"))
    result = extract_document(IMAGES, client, ocr_fn=ocr)
    assert result.method == "gemma_vision"
    assert "ocr_unavailable" in result.flags
    assert result.ocr_attempted is True


def test_ocr_failure_keeps_partial_result_and_flags_it():
    client = FakeClient([PARTIAL])
    result = extract_document(IMAGES, client, ocr_fn=FakeOCR(error=OCRError("boom")))
    assert "ocr_failed" in result.flags


def test_ocr_with_no_text_keeps_partial_result():
    client = FakeClient([PARTIAL])
    result = extract_document(IMAGES, client, ocr_fn=FakeOCR(text="   "))
    assert "ocr_no_text" in result.flags


def test_second_attempt_failure_keeps_first_result():
    client = FakeClient([PARTIAL, "garbage"])
    result = extract_document(IMAGES, client, ocr_fn=FakeOCR())
    assert result.method == "gemma_vision"
    assert "ocr_retry_failed" in result.flags


def test_no_usable_answer_and_ocr_failure_raises():
    client = FakeClient([ModelResponseError("bad")])
    with pytest.raises(ExtractionFailedError):
        extract_document(IMAGES, client, ocr_fn=FakeOCR(error=OCRError("boom")))


def test_nothing_usable_from_either_attempt_raises():
    client = FakeClient(["garbage", "still garbage"])
    with pytest.raises(ExtractionFailedError):
        extract_document(IMAGES, client, ocr_fn=FakeOCR())


def test_fallback_can_be_disabled():
    client = FakeClient([ModelResponseError("bad")])
    with pytest.raises(ExtractionFailedError):
        extract_document(IMAGES, client, ocr_fn=_forbidden_ocr, allow_ocr_fallback=False)


@pytest.mark.parametrize("error", [ModelUnavailableError("Ollama is down"), ModelCapabilityError("no vision")])
def test_model_unavailable_is_never_papered_over_with_ocr(error):
    client = FakeClient([error])
    with pytest.raises(type(error)):
        extract_document(IMAGES, client, ocr_fn=_forbidden_ocr)


def test_model_going_away_during_the_retry_still_raises():
    client = FakeClient([PARTIAL, ModelUnavailableError("gone")])
    with pytest.raises(ModelUnavailableError):
        extract_document(IMAGES, client, ocr_fn=FakeOCR())


# ---- records ---------------------------------------------------------------------------------------
def test_backend_owns_ids_and_validation_not_the_model():
    hostile = {
        **GOOD_INVOICE,
        "document_id": "doc_evil",
        "reviewed": True,
        "validation": {"schema_valid": True, "arithmetic_valid": True, "requires_human_review": False},
        "review_flags": [],
    }
    result = extract_document(IMAGES, FakeClient([hostile]), ocr_fn=_forbidden_ocr)
    record = build_record(result, "invoice.png", Decimal("1.00"))
    assert record.document_id.startswith("doc_") and record.document_id != "doc_evil"
    assert record.reviewed is False
    assert record.validation.requires_human_review is True


def test_build_record_calculates_validation_in_python():
    wrong_total = {**GOOD_INVOICE, "total_amount": "99999.00"}
    record = build_record(
        extract_document(IMAGES, FakeClient([wrong_total]), ocr_fn=_forbidden_ocr),
        "invoice.png",
        Decimal("1.00"),
    )
    assert record.validation.arithmetic_valid is False
    assert "total_mismatch" in record.review_flags
    assert record.field_review["total_amount"] is True


def test_mock_mode_is_labelled_and_flagged():
    result = extract_document(IMAGES, MockModelClient(), ocr_fn=_forbidden_ocr)
    assert result.method == "mock"
    assert "mock_extraction" in result.flags
