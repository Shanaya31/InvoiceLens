# Evaluation

The harness (`evaluation/run_evaluation.py`) runs the **real pipeline**: upload validation, image
preparation, model extraction, optional OCR retry, validation. It compares the result with verified
labels. It reports what it measures and nothing else. **No accuracy figure appears in this
repository until you have run it.**

## Dataset

- Target: **30 documents** covering digital invoices, scanned or photographed invoices, digital
  receipts and difficult receipts. The scaffold ships **6 synthetic starters**.
- Use synthetic or appropriately licensed documents only. Never publish client invoices, bank
  details, addresses or unredacted GST information.
- `manifest.csv` tags each file with a `category` and a `difficulty` (`clear` or `difficult`).
  Results are reported for all documents and **separately for clear and difficult ones**.
- Labels use `null` for values that are genuinely absent or unreadable. For example, a receipt
  printing `03/04/2026` has `invoice_date: null`, because the date is ambiguous.

## Configurations

Both run on the same documents so they can be compared directly:

| Name | Behaviour |
| --- | --- |
| `gemma_only` | Gemma vision, no OCR fallback |
| `gemma_plus_ocr` | Gemma vision, then one OCR-assisted retry when the first answer is unusable |

Running both calls the model twice per document. Use `--configs gemma_only` to halve the time.

## Metrics

Every metric is reported as `correct/total (percent)`. A rate with zero cases prints `n/a`.

| Metric | Definition |
| --- | --- |
| Critical-field accuracy | Documents where vendor name, date, invoice number **and** total all match the labels (a label of `null` must be predicted as `null`). Denominator: documents |
| Exact-match by field | Per-field match after trimming and collapsing whitespace. Vendor names ignore letter case; dates and amounts must match exactly. Denominator: documents |
| Numeric accuracy | Labelled (non-null) amount fields within `--numeric-tolerance` (default 1.00 INR). A missing prediction is wrong. Denominator: labelled amount fields |
| Missing-field handling | Fields labelled `null` that the model left empty. Hallucinated values (labelled `null`, predicted non-null) are counted separately. Denominator: labelled-null fields in successfully extracted documents |
| Review rate | Documents with at least one validation warning |
| Fallback rate | Documents where an OCR retry was attempted (always 0 for `gemma_only`) |
| Latency | Mean, median, p95 (nearest rank) and max seconds per document, from upload validation through the end of extraction, **including failures** |

Failed extractions count as **wrong** for every labelled field and are excluded from
missing-field handling, so a failure is never rewarded as "correctly left empty". The number of
failed extractions is printed with each group.

Document type and the expense category are subjective; the category is not scored.

## Reporting rules

- Always quote the denominator and the number of documents. Six documents cannot support a general
  accuracy claim.
- Separate clear from difficult documents. Do not average them into one headline.
- Report failures and hallucinations honestly; they matter more than the headline number.
- Record the model name, Ollama version, hardware and date next to any figure you publish. The
  harness writes the model and timestamp into its output files.
- `--client mock` only checks that the harness runs. Its numbers say nothing about any model.

## Running

```powershell
python sample_data/generate_samples.py
python evaluation/run_evaluation.py
```

Results land in `evaluation/results/` (git-ignored) as JSON (per-document detail) and Markdown.

## Growing the dataset

Add a record to `ground_truth/sample_annotations.jsonl` and a row to `manifest.csv`, then re-run the
generator. The generator refuses to draw a document whose line items do not add up to the label, so
labels and pages cannot drift apart. For real documents, keep them outside the repository, use
`--base-dir` to point at them, and write labels by hand from the original document.
