# InvoiceLens

**Your invoices stay yours.**

A local-first assistant that turns invoice and receipt images and PDFs into structured, editable
records with validation warnings and a reviewed CSV export. Built for Indian freelancers, small
businesses and small CA practices. Extraction runs on your own machine with an open-weight model
(Gemma 3 4B through Ollama), with Tesseract OCR as a fallback.

> **Status: working scaffold.** The full pipeline runs end to end and is covered by automated tests
> that use a fake model. It has **not** yet been measured against real model output; no accuracy
> claim is made anywhere in this repository. See [Evaluation](#evaluation).

## What it does

1. **Upload** JPG, PNG or PDF files (10 MB each, PDFs up to 5 pages).
2. **Extract** vendor, GSTIN, invoice number, date, subtotal, discount, CGST/SGST/IGST and total
   with Gemma vision. If the answer is unusable, run Tesseract and retry the model **once**.
3. **Validate** in Python (never by the model): required fields, ISO dates, decimal arithmetic with a
   rounding tolerance, GSTIN *format* (a valid format does not prove registration).
4. **Review**: edit any field, see warnings, save corrections (validation re-runs).
5. **Export** the corrected records to CSV.

Missing or unreadable values stay empty and are flagged. The app never guesses a GSTIN, invoice
number or total, and if the model is offline it says so instead of pretending a document was processed.

## Prerequisites

| Tool | Notes |
| --- | --- |
| Python 3.12+ | Backend |
| Node.js 20+ | Frontend |
| [Ollama](https://ollama.com) | `ollama pull gemma3:4b`, then keep Ollama running |
| [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html) | Optional but recommended: OCR fallback. Install separately and add it to `PATH` (or set `TESSERACT_CMD`) |

## Setup (Windows PowerShell)

Run from the repository root. Backend:

```powershell
py -3.12 --version
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Frontend, in a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. The dev server proxies `/api` to the backend on port 8000.

Optional: copy `.env.example` to `.env` to change the model, timeouts or limits.

### macOS / Linux

```bash
cd backend && python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest -q && .venv/bin/python -m uvicorn app.main:app --reload
# second terminal
cd frontend && npm install && npm run dev
```

### First milestone: try it with a synthetic invoice

```powershell
python sample_data/generate_samples.py      # creates sample_data/synthetic/
```

Upload `sample_data/synthetic/inv_clear_001.pdf`, check the extracted fields, edit one, click
**Save & mark reviewed**, then **Export CSV**. Before trusting the integration, confirm that
`GET /api/health` reports the model as ready and that image extraction and OCR both work on a
synthetic image **and** a synthetic PDF.

### Try the UI without a model

Set `EXTRACTION_MODE=mock` to get a fixed fake invoice for every upload. Records are labelled
`mock` and flagged, and the UI says "Mock extraction (not real inference)". It exists for UI
development and demos only.

## API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/health` | App status, model/OCR status, limits, `local_processing` flag |
| POST | `/api/documents` | Upload and extract one or more files (`multipart/form-data`, field `files`) |
| GET | `/api/documents` | List current records |
| GET | `/api/documents/{id}` | Fetch one record |
| PATCH | `/api/documents/{id}` | Save corrections (only sent fields change; validation re-runs) |
| DELETE | `/api/documents/{id}` | Delete one record |
| DELETE | `/api/documents` | Delete the whole batch |
| GET | `/api/export.csv` | CSV export (`?reviewed_only=true` to filter) |

Upload responses always have the shape `{"documents": [...], "errors": [...]}`. Each file succeeds or
fails on its own: HTTP 200 when at least one file succeeds, 422 when every file fails, 503 when the
model is unreachable. Interactive docs: <http://localhost:8000/docs>.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Where Ollama listens |
| `OLLAMA_MODEL` | `gemma3:4b` | Must support image input |
| `OLLAMA_TIMEOUT_SECONDS` | `90` | Per model call |
| `MAX_UPLOAD_MB` / `MAX_PDF_PAGES` | `10` / `5` | Upload limits |
| `EXTRACTION_MODE` | `gemma` | `gemma` (real) or `mock` (fixed fake data) |
| `ARITHMETIC_TOLERANCE` | `1.00` | INR tolerance for arithmetic checks |
| `TESSERACT_CMD` | (on PATH) | Full path to the Tesseract executable |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | CORS origin |

## Project layout

```
backend/    FastAPI app (api/, schemas/, services/) and pytest suite
frontend/   React + Vite dashboard
evaluation/ Manifest, ground truth and the evaluation harness
sample_data/ Synthetic document generator (never real documents)
docs/       Architecture, privacy and evaluation notes
```

## Tests

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
cd ..\frontend
npm run build
```

The tests never need Ollama or Tesseract: the model client and OCR are injected and replaced by
fakes, and the Ollama HTTP client is tested against a mock transport. One optional test uses the
real Tesseract binary and is skipped automatically when it is not installed. CI
(`.github/workflows/tests.yml`) runs the backend tests and the frontend build.

## Evaluation

```powershell
python sample_data/generate_samples.py
python evaluation/run_evaluation.py
```

Compares *Gemma only* with *Gemma + OCR fallback* on the same documents and reports critical-field
accuracy, per-field exact match, numeric accuracy, missing-field handling, review rate, fallback
rate and latency, each with its denominator and with clear and difficult documents separated. See
[`docs/evaluation.md`](docs/evaluation.md).

## Known limitations

- **Records are in memory only.** Restarting the backend loses the batch. Export before you stop it.
- **Single user, single process.** No accounts, no database, no multi-user separation.
- **Not accounting software.** No GST filing, ledger or payment features. A valid GSTIN *format* does
  not prove the number is registered.
- **Model output is untrusted.** Always review fields; the model can misread, especially on blurry
  photos, handwriting, or unusual layouts.
- **CPU-only machines are slow** with a vision model; the default timeout is 90 s per call.
- **English-script documents only** for the OCR fallback (`lang="eng"`).
- **Excel and leading zeros:** the CSV keeps invoice numbers like `000123` verbatim, but Excel may
  drop the zeros when you double-click the file. Import the column as Text.
- **Hosted demo** (see `render.yaml`, untested) uses mock extraction and synthetic data only.

## Licensing

The InvoiceLens source code is MIT licensed (see [LICENSE](LICENSE)). **Gemma and other model
weights have their own terms**, which that license does not override. Read the
[Gemma terms](https://ai.google.dev/gemma/terms) before using or redistributing the model.
Tesseract, Ollama and the other dependencies have their own licenses too.

## More

[Architecture](docs/architecture.md) · [Privacy](docs/privacy.md) · [Evaluation](docs/evaluation.md)
