"""Tests for the evaluation harness's scoring logic, using a fake model (no accuracy is measured)."""
from __future__ import annotations

import importlib.util
import json
from decimal import Decimal
from pathlib import Path

import pytest
from app.services.gemma import ModelUnavailableError
from fakes import GOOD_INVOICE, FakeClient, FakeOCR

HARNESS = Path(__file__).resolve().parents[2] / "evaluation" / "run_evaluation.py"
spec = importlib.util.spec_from_file_location("run_evaluation", HARNESS)
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)

TRUTH = {k: v for k, v in GOOD_INVOICE.items() if k not in ("currency", "expense_category")}
TOL = Decimal("1.00")


# ---- comparison helpers --------------------------------------------------------------------------------
def test_vendor_names_ignore_case_and_spacing_but_ids_do_not():
    assert harness.fields_match("vendor_name", "  sample   OFFICE supplies", "Sample Office Supplies")
    assert not harness.fields_match("invoice_number", "inv-1042", "INV-1042")
    assert harness.fields_match("invoice_number", " INV-1042 ", "INV-1042")


def test_null_must_match_null():
    assert harness.fields_match("invoice_number", None, None)
    assert not harness.fields_match("invoice_number", "X", None)
    assert not harness.fields_match("invoice_number", None, "X")


def test_numeric_tolerance():
    assert harness.numeric_match("11800.50", "11800.00", Decimal("1.00"))
    assert not harness.numeric_match("11802.00", "11800.00", Decimal("1.00"))
    assert not harness.numeric_match(None, "11800.00", Decimal("1.00"))


def test_percentile_nearest_rank():
    assert harness.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert harness.percentile([5.0], 95) == 5.0
    assert harness.percentile([], 95) is None


# ---- scoring -----------------------------------------------------------------------------------------------
def test_perfect_prediction():
    score = harness.score_document(dict(GOOD_INVOICE), TRUTH, TOL)
    assert score["critical"] is True
    assert score["hallucinated"] == []
    assert all(score["exact"].values())


def test_hallucinated_value_is_detected():
    truth = {**TRUTH, "vendor_gstin": None}
    score = harness.score_document(dict(GOOD_INVOICE), truth, TOL)  # model still returns a GSTIN
    assert score["hallucinated"] == ["vendor_gstin"]
    assert score["null_ok"]["vendor_gstin"] is False


def test_wrong_critical_field_fails_the_document():
    score = harness.score_document({**GOOD_INVOICE, "total_amount": "11801.00"}, TRUTH, TOL)
    assert score["critical"] is False
    assert score["exact"]["total_amount"] is False
    assert score["numeric"]["total_amount"] is True  # within the 1.00 tolerance


def test_failed_extraction_is_wrong_not_correctly_empty():
    truth = {**TRUTH, "vendor_gstin": None}
    score = harness.score_document(None, truth, TOL)
    assert score["critical"] is False
    assert score["exact"]["vendor_name"] is False
    assert score["null_ok"] == {}  # excluded from missing-field handling


# ---- end to end with a fake model ------------------------------------------------------------------------
@pytest.fixture
def dataset(tmp_path, make_png):
    base = tmp_path / "docs"
    base.mkdir()
    (base / "a.png").write_bytes(make_png())
    (base / "b.png").write_bytes(make_png())
    manifest = [
        {"file": "a.png", "category": "digital_invoice", "difficulty": "clear", "notes": ""},
        {"file": "b.png", "category": "scanned_invoice", "difficulty": "difficult", "notes": ""},
    ]
    return base, manifest, {"a.png": TRUTH, "b.png": TRUTH}


def test_evaluate_reports_denominators_and_separates_difficulty(dataset):
    base, manifest, truth = dataset
    client = FakeClient([GOOD_INVOICE, {**GOOD_INVOICE, "vendor_name": "Wrong Vendor"}])
    report = harness.evaluate(manifest, truth, client, base_dir=base, configs=["gemma_only"])
    groups = report["configs"]["gemma_only"]["groups"]
    assert set(groups) == {"all", "clear", "difficult"}
    assert groups["all"]["critical_field_accuracy"] == {"correct": 1, "total": 2, "rate": 0.5}
    assert groups["clear"]["critical_field_accuracy"]["rate"] == 1.0
    assert groups["difficult"]["critical_field_accuracy"]["rate"] == 0.0
    assert groups["all"]["fallback_rate"]["correct"] == 0
    assert groups["all"]["latency_seconds"]["n"] == 2


def test_evaluate_counts_fallback_and_failures(dataset):
    base, manifest, truth = dataset
    # doc a: unusable first answer, OCR retry recovers. doc b: model down -> failed extraction.
    client = FakeClient(["not json", GOOD_INVOICE, ModelUnavailableError("down")])
    report = harness.evaluate(
        manifest, truth, client, base_dir=base, configs=["gemma_plus_ocr"], ocr_fn=FakeOCR()
    )
    overall = report["configs"]["gemma_plus_ocr"]["groups"]["all"]
    assert overall["fallback_rate"]["correct"] == 1
    assert overall["failed_extractions"] == 1
    assert overall["critical_field_accuracy"]["correct"] == 1
    failed = [d for d in report["configs"]["gemma_plus_ocr"]["documents"] if d["status"] == "failed"]
    assert len(failed) == 1 and "ModelUnavailableError" in failed[0]["error"]


def test_gemma_only_never_calls_ocr(dataset):
    base, manifest, truth = dataset

    def forbidden(images):
        raise AssertionError("OCR must not run in the gemma_only configuration")

    client = FakeClient(["not json", "still not json"])
    report = harness.evaluate(manifest, truth, client, base_dir=base, configs=["gemma_only"], ocr_fn=forbidden)
    assert report["configs"]["gemma_only"]["groups"]["all"]["failed_extractions"] == 2


def test_missing_files_and_labels_fail_fast(dataset, tmp_path):
    base, manifest, truth = dataset
    with pytest.raises(SystemExit, match="generate_samples"):
        harness.evaluate(manifest, truth, FakeClient(), base_dir=tmp_path / "nowhere", configs=["gemma_only"])
    with pytest.raises(SystemExit, match="No ground-truth labels"):
        harness.evaluate(manifest, {"a.png": TRUTH}, FakeClient(), base_dir=base, configs=["gemma_only"])


def test_markdown_report_flags_mock_client(dataset):
    base, manifest, truth = dataset
    report = harness.evaluate(manifest, truth, FakeClient([GOOD_INVOICE] * 2), base_dir=base, configs=["gemma_only"])
    meta = {"generated": "now", "model": "mock", "client": "mock", "documents": 2, "tolerance": "1.00"}
    text = harness.render_markdown(report, meta)
    assert "MOCK CLIENT" in text and "2/2 (100.0%)" in text
    json.dumps(report)  # the report must be JSON-serialisable


def test_shipped_manifest_and_labels_are_consistent():
    root = HARNESS.parent
    manifest = harness.load_manifest(root / "manifest.csv")
    labels = harness.load_annotations(root / "ground_truth" / "sample_annotations.jsonl")
    assert [row["file"] for row in manifest] == list(labels)
    assert {row["difficulty"] for row in manifest} <= {"clear", "difficult"}
