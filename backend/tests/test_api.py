"""API behaviour end to end, with the model and OCR replaced by fakes."""
from __future__ import annotations

import csv
import io

from app.services.gemma import ModelUnavailableError
from app.services.ocr import OCRError
from fakes import GOOD_INVOICE


def upload(client, files):
    """files: list of (filename, bytes, mime)."""
    return client.post("/api/documents", files=[("files", f) for f in files])


def png_file(make_png, name="invoice.png"):
    return (name, make_png(), "image/png")


def read_csv(response):
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))


# ---- upload ------------------------------------------------------------------------------------------
def test_upload_creates_record_with_backend_owned_fields(client, fake_client, make_png):
    fake_client.responses = [GOOD_INVOICE]
    response = upload(client, [png_file(make_png)])
    assert response.status_code == 200
    body = response.json()
    assert body["errors"] == []
    record = body["documents"][0]
    assert record["document_id"].startswith("doc_")
    assert record["filename"] == "invoice.png"
    assert record["extraction_method"] == "gemma_vision"
    assert record["vendor_name"] == "Sample Office Supplies"
    assert record["total_amount"] == "11800.00"
    assert record["validation"] == {"schema_valid": True, "arithmetic_valid": True, "requires_human_review": True}
    assert record["reviewed"] is False


def test_money_is_returned_as_strings(client, fake_client, make_png):
    fake_client.responses = [{**GOOD_INVOICE, "total_amount": 11800.5, "subtotal": 10000.5, "total_tax_amount": 1800}]
    record = upload(client, [png_file(make_png)]).json()["documents"][0]
    assert record["total_amount"] == "11800.50"
    assert isinstance(record["subtotal"], str)


def test_jpeg_and_pdf_are_accepted(client, fake_client, make_jpeg, make_pdf):
    fake_client.responses = [GOOD_INVOICE, GOOD_INVOICE]
    response = upload(
        client,
        [("a.jpg", make_jpeg(), "image/jpeg"), ("b.pdf", make_pdf(2), "application/pdf")],
    )
    assert response.status_code == 200
    assert len(response.json()["documents"]) == 2
    # the PDF's two pages are sent to the model together
    assert [call[0] for call in fake_client.calls] == [1, 2]


def test_partial_failure_reports_each_file_separately(client, fake_client, make_png):
    fake_client.responses = [GOOD_INVOICE]
    response = upload(client, [png_file(make_png), ("notes.txt", b"hello", "text/plain")])
    assert response.status_code == 200
    body = response.json()
    assert len(body["documents"]) == 1
    assert [(e["filename"], e["code"]) for e in body["errors"]] == [("notes.txt", "unsupported_type")]
    assert len(client.get("/api/documents").json()) == 1


def test_all_files_rejected_returns_422_and_creates_nothing(client):
    response = upload(client, [("a.txt", b"x", "text/plain"), ("b.gif", b"GIF89a", "image/gif")])
    assert response.status_code == 422
    assert len(response.json()["errors"]) == 2
    assert client.get("/api/documents").json() == []


def test_model_offline_is_a_clear_error_and_never_a_fake_success(client, fake_client, make_png):
    fake_client.responses = [ModelUnavailableError("Cannot reach Ollama at http://localhost:11434. Start Ollama.")]
    response = upload(client, [png_file(make_png)])
    assert response.status_code == 503
    body = response.json()
    assert body["documents"] == []
    assert body["errors"][0]["code"] == "model_unavailable"
    assert "Start Ollama" in body["errors"][0]["message"]
    assert client.get("/api/documents").json() == []


def test_ocr_fallback_is_used_through_the_api(client, fake_client, fake_ocr, make_png):
    fake_client.responses = ["not json", GOOD_INVOICE]
    record = upload(client, [png_file(make_png)]).json()["documents"][0]
    assert record["extraction_method"] == "ocr_gemma"
    assert fake_ocr.calls == 1


def test_extraction_failure_is_reported_per_file(client, fake_client, fake_ocr, make_png):
    fake_ocr.error = OCRError("boom")
    fake_client.responses = ["not json"]
    response = upload(client, [png_file(make_png)])
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "extraction_failed"
    assert client.get("/api/documents").json() == []


def test_unexpected_errors_do_not_leak_details(client, fake_client, make_png):
    # No scripted responses: FakeClient raises AssertionError, an "unexpected" failure.
    response = upload(client, [png_file(make_png)])
    error = response.json()["errors"][0]
    assert error["code"] == "processing_error"
    assert "AssertionError" not in error["message"] and "FakeClient" not in error["message"]


# ---- read ----------------------------------------------------------------------------------------------
def test_list_and_get(client, fake_client, make_png):
    fake_client.responses = [GOOD_INVOICE, GOOD_INVOICE]
    ids = [d["document_id"] for d in upload(client, [png_file(make_png, "a.png"), png_file(make_png, "b.png")]).json()["documents"]]
    assert [d["document_id"] for d in client.get("/api/documents").json()] == ids
    assert client.get(f"/api/documents/{ids[0]}").json()["filename"] == "a.png"


def test_get_unknown_document_is_404(client):
    assert client.get("/api/documents/doc_missing").status_code == 404


# ---- corrections -----------------------------------------------------------------------------------------
def _one_record(client, fake_client, make_png, data=GOOD_INVOICE):
    fake_client.responses = [data]
    return upload(client, [png_file(make_png)]).json()["documents"][0]


def test_patch_saves_correction_and_reruns_validation(client, fake_client, make_png):
    record = _one_record(client, fake_client, make_png)
    response = client.patch(f"/api/documents/{record['document_id']}", json={"total_amount": "12000"})
    assert response.status_code == 200
    updated = response.json()
    assert updated["total_amount"] == "12000.00"
    assert updated["corrected_fields"] == ["total_amount"]
    assert updated["reviewed"] is True
    assert updated["validation"]["arithmetic_valid"] is False
    assert "total_mismatch" in updated["review_flags"]
    assert updated["vendor_name"] == record["vendor_name"]  # untouched fields are preserved
    assert client.get(f"/api/documents/{record['document_id']}").json()["total_amount"] == "12000.00"


def test_fixing_the_arithmetic_clears_the_warning(client, fake_client, make_png):
    record = _one_record(client, fake_client, make_png, {**GOOD_INVOICE, "total_amount": "99999.00"})
    assert "total_mismatch" in record["review_flags"]
    fixed = client.patch(f"/api/documents/{record['document_id']}", json={"total_amount": "11800.00"}).json()
    assert "total_mismatch" not in fixed["review_flags"]
    assert fixed["validation"]["arithmetic_valid"] is True


def test_correction_resolves_extraction_stage_flag(client, fake_client, make_png):
    ambiguous = {**GOOD_INVOICE, "invoice_date": "03/04/2026"}
    # A missing date makes the first attempt unusable, so the OCR-assisted retry (also ambiguous) runs.
    fake_client.responses = [ambiguous, ambiguous]
    record = upload(client, [png_file(make_png)]).json()["documents"][0]
    assert "invoice_date_ambiguous" in record["review_flags"]
    fixed = client.patch(f"/api/documents/{record['document_id']}", json={"invoice_date": "2026-04-03"}).json()
    assert fixed["invoice_date"] == "2026-04-03"
    assert "invoice_date_ambiguous" not in fixed["review_flags"]


def test_clearing_a_field_is_possible_and_revalidated(client, fake_client, make_png):
    record = _one_record(client, fake_client, make_png)
    updated = client.patch(f"/api/documents/{record['document_id']}", json={"vendor_gstin": None}).json()
    assert updated["vendor_gstin"] is None
    assert "vendor_gstin_missing" in updated["review_flags"]


def test_patch_rejects_invalid_values(client, fake_client, make_png):
    record = _one_record(client, fake_client, make_png)
    url = f"/api/documents/{record['document_id']}"
    assert client.patch(url, json={"invoice_date": "12/10/2026"}).status_code == 422
    assert client.patch(url, json={"total_amount": "lots"}).status_code == 422
    assert client.patch(url, json={"document_type": "spreadsheet"}).status_code == 422
    assert client.patch(url, json={"document_type": None}).status_code == 422
    assert client.get(url).json()["total_amount"] == "11800.00"  # nothing was half-applied


def test_patch_cannot_touch_backend_owned_fields(client, fake_client, make_png):
    record = _one_record(client, fake_client, make_png)
    url = f"/api/documents/{record['document_id']}"
    body = client.patch(url, json={"document_id": "doc_evil", "review_flags": [], "extraction_method": "mock"}).json()
    assert body["document_id"] == record["document_id"]
    assert body["extraction_method"] == "gemma_vision"


def test_patch_unknown_document_is_404(client):
    assert client.patch("/api/documents/doc_missing", json={"vendor_name": "x"}).status_code == 404


# ---- deletion ----------------------------------------------------------------------------------------------
def test_delete_one_document(client, fake_client, make_png):
    record = _one_record(client, fake_client, make_png)
    assert client.delete(f"/api/documents/{record['document_id']}").status_code == 204
    assert client.get(f"/api/documents/{record['document_id']}").status_code == 404
    assert client.delete(f"/api/documents/{record['document_id']}").status_code == 404


def test_delete_whole_batch(client, fake_client, make_png):
    fake_client.responses = [GOOD_INVOICE, GOOD_INVOICE]
    upload(client, [png_file(make_png, "a.png"), png_file(make_png, "b.png")])
    assert client.delete("/api/documents").status_code == 204
    assert client.get("/api/documents").json() == []


# ---- export -------------------------------------------------------------------------------------------------
def test_export_contains_corrected_values_not_the_originals(client, fake_client, make_png):
    record = _one_record(client, fake_client, make_png)
    client.patch(f"/api/documents/{record['document_id']}", json={"vendor_name": "Corrected Vendor Pvt Ltd"})
    response = client.get("/api/export.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert response.content.startswith(b"\xef\xbb\xbf")  # BOM so Excel reads UTF-8
    rows = read_csv(response)
    assert len(rows) == 1
    assert rows[0]["vendor_name"] == "Corrected Vendor Pvt Ltd"
    assert "Sample Office Supplies" not in response.text
    assert rows[0]["reviewed"] == "true"
    assert rows[0]["total_amount"] == "11800.00"


def test_export_reviewed_only(client, fake_client, make_png):
    fake_client.responses = [GOOD_INVOICE, GOOD_INVOICE]
    docs = upload(client, [png_file(make_png, "a.png"), png_file(make_png, "b.png")]).json()["documents"]
    client.patch(f"/api/documents/{docs[0]['document_id']}", json={"reviewed": True})
    assert len(read_csv(client.get("/api/export.csv"))) == 2
    only = read_csv(client.get("/api/export.csv?reviewed_only=true"))
    assert [row["document_id"] for row in only] == [docs[0]["document_id"]]


def test_export_with_no_records_is_just_a_header(client):
    rows = read_csv(client.get("/api/export.csv"))
    assert rows == []
    assert client.get("/api/export.csv").text.lstrip("﻿").startswith("document_id,filename")
