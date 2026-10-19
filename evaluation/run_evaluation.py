#!/usr/bin/env python3
"""Repeatable evaluation harness for InvoiceLens.

Runs the real extraction pipeline over a manifest of documents and compares the output with
verified ground-truth labels. It reports numbers; it never invents them. Every metric shows its
denominator, and clear documents are reported separately from difficult ones.

    python evaluation/run_evaluation.py                 # real model via Ollama, both configurations
    python evaluation/run_evaluation.py --configs gemma_only
    python evaluation/run_evaluation.py --client mock   # harness smoke test ONLY (metrics are meaningless)

Configurations (run on the same documents):
    gemma_only      Gemma vision, no OCR fallback
    gemma_plus_ocr  Gemma vision, then a single OCR-assisted retry when the first answer is unusable
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.schemas.document import MONEY_FIELDS  # noqa: E402
from app.services.extraction import ExtractionFailedError, build_record, extract_document  # noqa: E402
from app.services.gemma import GemmaError  # noqa: E402
from app.services.ocr import ocr_images  # noqa: E402
from app.services.uploads import prepare_images, validate_upload  # noqa: E402

CRITICAL_FIELDS = ("vendor_name", "invoice_date", "invoice_number", "total_amount")
TEXT_FIELDS = ("document_type", "vendor_name", "vendor_gstin", "invoice_number", "invoice_date")
SCORED_FIELDS = (*TEXT_FIELDS, *MONEY_FIELDS)
CONFIGS = {"gemma_only": False, "gemma_plus_ocr": True}  # name -> allow_ocr_fallback
MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 5


# ---- comparison helpers ------------------------------------------------------------------------------
def _norm_text(value: Optional[str], *, fold: bool) -> Optional[str]:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text.casefold() if fold else text


def fields_match(name: str, predicted: Optional[str], truth: Optional[str]) -> bool:
    """Exact match after trimming/collapsing whitespace. Vendor names ignore letter case."""
    if name in MONEY_FIELDS or name in ("invoice_date", "document_type"):
        return predicted == truth
    return _norm_text(predicted, fold=name == "vendor_name") == _norm_text(truth, fold=name == "vendor_name")


def numeric_match(predicted: Optional[str], truth: Optional[str], tolerance: Decimal) -> bool:
    if predicted is None or truth is None:
        return predicted is None and truth is None
    try:
        return abs(Decimal(predicted) - Decimal(truth)) <= tolerance
    except InvalidOperation:
        return False


def ratio(correct: int, total: int) -> Dict[str, Any]:
    return {"correct": correct, "total": total, "rate": (correct / total) if total else None}


def percentile(values: List[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


# ---- scoring ---------------------------------------------------------------------------------------------
def score_document(predicted: Optional[Dict[str, Any]], truth: Dict[str, Any], tolerance: Decimal) -> Dict[str, Any]:
    """Compare one prediction (``None`` when extraction failed) with its labels."""
    failed = predicted is None
    exact: Dict[str, bool] = {}
    numeric: Dict[str, bool] = {}
    null_ok: Dict[str, bool] = {}
    hallucinated: List[str] = []
    for name in SCORED_FIELDS:
        expected = truth.get(name)
        got = None if failed else predicted.get(name)
        exact[name] = False if failed and expected is not None else fields_match(name, got, expected)
        if name in MONEY_FIELDS and expected is not None:
            numeric[name] = False if failed else numeric_match(got, expected, tolerance)
        if expected is None and not failed:
            null_ok[name] = got is None
            if got is not None:
                hallucinated.append(name)
    critical = all(exact[name] for name in CRITICAL_FIELDS)
    return {"exact": exact, "numeric": numeric, "null_ok": null_ok, "hallucinated": hallucinated, "critical": critical}


def aggregate(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate per-document results. Failed documents count as wrong, never as 'correctly empty'."""
    per_field = {}
    for name in SCORED_FIELDS:
        per_field[name] = ratio(sum(r["score"]["exact"][name] for r in results), len(results))
    numeric_pairs = [ok for r in results for ok in r["score"]["numeric"].values()]
    null_pairs = [ok for r in results for ok in r["score"]["null_ok"].values()]
    latencies = [r["latency_seconds"] for r in results]
    return {
        "documents": len(results),
        "failed_extractions": sum(1 for r in results if r["status"] == "failed"),
        "critical_field_accuracy": ratio(sum(r["score"]["critical"] for r in results), len(results)),
        "exact_match_by_field": per_field,
        "numeric_accuracy": ratio(sum(numeric_pairs), len(numeric_pairs)),
        "missing_field_handling": {
            **ratio(sum(null_pairs), len(null_pairs)),
            "hallucinated_values": len(null_pairs) - sum(null_pairs),
        },
        "review_rate": ratio(sum(1 for r in results if r["review_flags"]), len(results)),
        "fallback_rate": ratio(sum(1 for r in results if r["ocr_attempted"]), len(results)),
        "latency_seconds": {
            "n": len(latencies),
            "mean": statistics.fmean(latencies) if latencies else None,
            "median": statistics.median(latencies) if latencies else None,
            "p95": percentile(latencies, 95),
            "max": max(latencies) if latencies else None,
        },
    }


# ---- running -----------------------------------------------------------------------------------------------
def load_manifest(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_annotations(path: Path) -> Dict[str, Dict[str, Any]]:
    labels: Dict[str, Dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                labels[record["file"]] = record["fields"]
    return labels


def run_document(
    path: Path,
    client: Any,
    *,
    allow_ocr: bool,
    ocr_fn: Callable[[List[bytes]], str],
    tolerance: Decimal,
) -> Dict[str, Any]:
    """Process one file through the real pipeline and time it (failures included)."""
    started = time.perf_counter()
    outcome: Dict[str, Any] = {"status": "ok", "error": None, "ocr_attempted": False, "review_flags": [],
                               "predicted": None, "method": None}
    try:
        validated = validate_upload(path.name, path.read_bytes(), max_bytes=MAX_BYTES, max_pages=MAX_PAGES)
        images = prepare_images(validated, MAX_PAGES)
        result = extract_document(images, client, ocr_fn=ocr_fn, allow_ocr_fallback=allow_ocr)
        record = build_record(result, path.name, tolerance)
        outcome.update(
            predicted=record.model_dump(),
            method=record.extraction_method,
            ocr_attempted=result.ocr_attempted,
            review_flags=record.review_flags,
        )
    except (ExtractionFailedError, GemmaError) as exc:
        outcome.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # keep going: one bad file must not abort the whole run
        outcome.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    outcome["latency_seconds"] = time.perf_counter() - started
    return outcome


def evaluate(
    manifest: List[Dict[str, str]],
    annotations: Dict[str, Dict[str, Any]],
    client: Any,
    *,
    base_dir: Path,
    configs: List[str],
    tolerance: Decimal = Decimal("1.00"),
    ocr_fn: Callable[[List[bytes]], str] = ocr_images,
) -> Dict[str, Any]:
    missing_labels = [row["file"] for row in manifest if row["file"] not in annotations]
    if missing_labels:
        raise SystemExit(f"No ground-truth labels for: {', '.join(missing_labels)}")
    missing_files = [row["file"] for row in manifest if not (base_dir / row["file"]).is_file()]
    if missing_files:
        raise SystemExit(
            f"Missing document files: {', '.join(missing_files)}\n"
            "Generate the synthetic set with: python sample_data/generate_samples.py"
        )

    report: Dict[str, Any] = {"configs": {}}
    for config in configs:
        per_doc: List[Dict[str, Any]] = []
        for row in manifest:
            outcome = run_document(base_dir / row["file"], client, allow_ocr=CONFIGS[config], ocr_fn=ocr_fn,
                                   tolerance=tolerance)
            truth = annotations[row["file"]]
            outcome.update(
                file=row["file"],
                category=row["category"],
                difficulty=row["difficulty"],
                score=score_document(outcome["predicted"], truth, tolerance),
            )
            per_doc.append(outcome)
        groups = {"all": per_doc}
        for level in sorted({r["difficulty"] for r in per_doc}):
            groups[level] = [r for r in per_doc if r["difficulty"] == level]
        report["configs"][config] = {
            "groups": {name: aggregate(items) for name, items in groups.items()},
            "documents": [
                {k: v for k, v in r.items() if k not in ("score",)}
                | {"critical_correct": r["score"]["critical"], "hallucinated_fields": r["score"]["hallucinated"]}
                for r in per_doc
            ],
        }
    return report


# ---- reporting ---------------------------------------------------------------------------------------------
def fmt(item: Dict[str, Any]) -> str:
    if item["rate"] is None:
        return "n/a (0 cases)"
    return f"{item['correct']}/{item['total']} ({item['rate'] * 100:.1f}%)"


def fmt_seconds(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.1f}s"


def render_markdown(report: Dict[str, Any], meta: Dict[str, Any]) -> str:
    lines = [
        "# InvoiceLens evaluation results",
        "",
        f"- Generated: {meta['generated']}",
        f"- Model: {meta['model']}  |  Client: {meta['client']}  |  Documents: {meta['documents']}",
        f"- Numeric tolerance: {meta['tolerance']} INR",
    ]
    if meta["client"] == "mock":
        lines += ["", "> **MOCK CLIENT: harness smoke test only. These numbers say nothing about model accuracy.**"]
    for config, data in report["configs"].items():
        lines += ["", f"## {config}", ""]
        for group, m in data["groups"].items():
            lines += [
                f"### {group} documents (n={m['documents']}, failed extractions: {m['failed_extractions']})",
                "",
                "| Metric | Result |",
                "| --- | --- |",
                f"| Critical-field accuracy (vendor, date, invoice no., total all correct) | {fmt(m['critical_field_accuracy'])} |",
                f"| Numeric accuracy (labelled amounts within tolerance) | {fmt(m['numeric_accuracy'])} |",
                f"| Missing-field handling (labelled-empty fields left empty) | {fmt(m['missing_field_handling'])}, hallucinated: {m['missing_field_handling']['hallucinated_values']} |",
                f"| Review rate (documents with at least one warning) | {fmt(m['review_rate'])} |",
                f"| Fallback rate (OCR retry attempted) | {fmt(m['fallback_rate'])} |",
                f"| Latency mean / median / p95 / max | {fmt_seconds(m['latency_seconds']['mean'])} / {fmt_seconds(m['latency_seconds']['median'])} / {fmt_seconds(m['latency_seconds']['p95'])} / {fmt_seconds(m['latency_seconds']['max'])} |",
                "",
                "| Field | Exact match |",
                "| --- | --- |",
                *[f"| {name} | {fmt(item)} |" for name, item in m["exact_match_by_field"].items()],
                "",
            ]
    return "\n".join(lines)


def build_client(args: argparse.Namespace):
    if args.client == "mock":
        from app.services.gemma import MockModelClient

        print("WARNING: using the MOCK client. Metrics are meaningless; this only exercises the harness.\n")
        return MockModelClient()
    from app.config import get_settings
    from app.services.gemma import OllamaClient

    settings = get_settings()
    client = OllamaClient(args.ollama_url or settings.ollama_base_url, args.model or settings.ollama_model,
                          args.timeout or settings.ollama_timeout_seconds)
    status = client.check_model()
    if not (status.reachable and status.available):
        raise SystemExit(f"Model is not ready: {status.detail}")
    if status.supports_vision is False:
        raise SystemExit(status.detail)
    print(status.detail)
    return client


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, default=ROOT / "evaluation" / "manifest.csv")
    parser.add_argument("--annotations", type=Path, default=ROOT / "evaluation" / "ground_truth" / "sample_annotations.jsonl")
    parser.add_argument("--base-dir", type=Path, default=ROOT / "sample_data", help="Folder the manifest paths are relative to")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "evaluation" / "results")
    parser.add_argument("--configs", nargs="+", choices=list(CONFIGS), default=list(CONFIGS))
    parser.add_argument("--client", choices=["ollama", "mock"], default="ollama")
    parser.add_argument("--ollama-url")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--numeric-tolerance", type=Decimal, default=Decimal("1.00"), help="INR; default 1.00")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    annotations = load_annotations(args.annotations)
    client = build_client(args)
    report = evaluate(manifest, annotations, client, base_dir=args.base_dir, configs=args.configs,
                      tolerance=args.numeric_tolerance)

    meta = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": getattr(client, "model", "mock"),
        "client": args.client,
        "documents": len(manifest),
        "tolerance": str(args.numeric_tolerance),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (args.output_dir / f"results_{stamp}.json").write_text(json.dumps({"meta": meta, **report}, indent=2), encoding="utf-8")
    markdown = render_markdown(report, meta)
    (args.output_dir / f"results_{stamp}.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"\nSaved to {args.output_dir}/results_{stamp}.(json|md)")


if __name__ == "__main__":
    main()
