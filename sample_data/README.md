# Sample data

**Only synthetic documents belong in this repository.** Never commit real invoices, receipts,
client names, addresses, bank details or unredacted GST numbers.

## Generate the synthetic set

From the repository root (with the backend virtual environment active, or any Python that has
`Pillow` and `PyMuPDF` installed):

```powershell
python sample_data/generate_samples.py
```

This writes PDFs, PNGs and degraded "scan/photo" JPEGs to `sample_data/synthetic/` (git-ignored).
Each document is drawn from `evaluation/ground_truth/sample_annotations.jsonl`, so the labels
match the printed values by construction. Vendor names, GSTINs and addresses are invented. The
GSTINs have a valid *format* but are not real registrations.

The six starter documents cover:

| File | What it exercises |
| --- | --- |
| `inv_clear_001.pdf` | Digital PDF, CGST + SGST |
| `inv_clear_002.png` | Clean image, IGST, invoice number with leading zeros |
| `receipt_clear_001.pdf` | Receipt with no GSTIN and no invoice number |
| `inv_scan_001.jpg` | Rotated, noisy, low-quality scan with a discount |
| `receipt_difficult_001.jpg` | Blurry receipt whose date `03/04/2026` is ambiguous (correct answer: empty) |
| `inv_clear_003.png` | Unregistered vendor: no GSTIN, no tax |

The evaluation target is 30 documents. To grow the set, add records to the annotations file and
rows to `evaluation/manifest.csv`, then re-run the generator. If you use real documents, keep them
**outside** the repository and point `--base-dir` at that folder; never publish them.
