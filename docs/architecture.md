# Architecture

InvoiceLens keeps AI, OCR and validation in separate modules so each can be tested on its own.

```
Browser (React)  --/api-->  FastAPI
                              |
        upload ──► uploads.py     size, extension, file signature, real parsing, page limit
                              |
                   pdf_renderer.py   PDF pages / images -> normalised PNG bytes (in memory)
                              |
                   extraction.py     orchestrates the workflow below
                     |        |
                gemma.py     ocr.py        model client (Ollama) / Tesseract wrapper
                     |
                   validation.py   Decimal arithmetic, dates, GSTIN format, flags (Python only)
                              |
                   store.py         in-memory records
                              |
                   csv_export.py    corrected records -> CSV
```

## Extraction workflow

1. **Vision attempt.** Prepared page images go to Gemma through Ollama with a prompt that forbids
   guessing and asks for one JSON object.
2. **Usable?** An answer is usable when vendor, date and total are present (and an invoice number
   for invoices). Otherwise, and on malformed JSON or a timeout:
3. **OCR retry, once.** Tesseract text is added to the prompt (marked as untrusted data) and the
   model is called one more time without images. The better-scoring attempt wins.
4. **Never invent.** Fields that are unreadable become `null` plus a review flag. If neither
   attempt produces anything usable the file fails with a clear per-file error.
5. **Model unavailable** (Ollama down, model not installed, or the model lacks vision) fails fast
   with an actionable message. OCR cannot paper over that, and no fake success is ever returned.

## Design decisions

- **The backend owns truth.** IDs, review flags and validation are computed in Python. Anything
  the model sends beyond the extraction fields (`document_id`, `validation`, `reviewed`...) is
  ignored.
- **Money is `Decimal` or a decimal string**, never binary floating point. JSON numbers from the
  model are parsed with `Decimal`, so `0.10` stays `0.10`.
- **Dates are ISO only.** `03/04/2026` is ambiguous, so it becomes `null` with an
  `invoice_date_ambiguous` flag instead of a guess.
- **Per-field sanitising.** One bad value is nulled and flagged without discarding the rest.
- **Corrections are preserved.** Saving a field records it in `corrected_fields`, re-runs
  validation, and clears extraction-stage warnings for fields the user fixed.
- **Injectable dependencies.** `get_model_client`, `get_ocr_fn`, `get_store` and `get_settings` are
  FastAPI dependencies, so tests swap in fakes and need no Ollama or Tesseract.
- **Honest labelling.** `EXTRACTION_MODE=mock` is explicit; mock records say `mock`, carry a
  `mock_extraction` flag, and `local_processing` is never reported as true for them. The health
  endpoint only claims local inference when Ollama is configured on a loopback address.
- **No temp files.** Everything is processed in memory, so there is nothing to clean up on failure.

## Extension points

| Goal | Where |
| --- | --- |
| Different or additional model | `services/gemma.py` (implement `extract` and `check_model`) |
| Better OCR / other languages | `services/ocr.py` |
| New fields | `schemas/document.py`, the prompt in `gemma.py`, `validation.py`, `csv_export.py`, the form |
| Other export formats | `services/csv_export.py`, `api/export.py` |
| Persistence | Replace `services/store.py` (same small interface) |
| Hosted demo | `render.yaml` (mock mode, synthetic data only; untested) |
