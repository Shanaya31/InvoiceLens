# Evaluation

A repeatable harness that runs the **real** extraction pipeline over a manifest of documents and
compares it with verified labels. It reports measured numbers only. See
[`docs/evaluation.md`](../docs/evaluation.md) for metric definitions and reporting rules.

| Path | Purpose |
| --- | --- |
| `manifest.csv` | One row per document: `file`, `category`, `difficulty` (`clear` or `difficult`), `notes` |
| `ground_truth/sample_annotations.jsonl` | Verified labels per file, plus layout data used only to draw the synthetic sample |
| `run_evaluation.py` | The harness |
| `results/` | Output (JSON and Markdown). Git-ignored |

## Run it

```powershell
python sample_data/generate_samples.py          # once, creates the synthetic documents
ollama pull gemma3:4b                           # once; keep Ollama running
python evaluation/run_evaluation.py             # compares gemma_only and gemma_plus_ocr
python evaluation/run_evaluation.py --configs gemma_only
python evaluation/run_evaluation.py --client mock   # smoke-tests the harness; metrics are meaningless
```

The starter set has 6 documents; the target is 30. Do not quote accuracy from 6 documents as a
general result. Always quote the denominator.
