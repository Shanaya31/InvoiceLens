# Privacy

## The local workflow (primary)

When you run InvoiceLens on your own machine with Ollama on `localhost`:

- Documents are read into memory, processed by the model on your machine, and kept **only in the
  backend process's memory**. Nothing is written to disk by the app, and there are no temporary files.
- Records disappear when the backend stops. The CSV you export is the only copy the app creates,
  and it goes where your browser saves downloads.
- No analytics, accounts or third-party API calls are made. The UI shows **Local processing** only
  when the backend is configured for real inference on a loopback address.

## Logging

The backend logs error **codes** and exception **types** only. It never logs filenames, document
contents, OCR text or extracted values. This is covered by an automated test
(`test_nothing_sensitive_is_logged`). If you add logging, keep to that rule.

## What the app cannot promise

- **Your machine is still your machine.** Anyone with access to it, other local processes, backups or
  swap files may reach data the app holds in memory.
- **Model terms.** Check the license and terms of the model you run. They apply to the model, not to
  this code.
- **Review before relying.** Extracted values can be wrong. Treat them as a draft.
- **GSTIN format checks are not registration checks.** A well-formed number may not exist.

## Hosted demos

A hosted deployment (for example the optional `render.yaml`) is a different privacy situation:

- Use **synthetic documents only**. Do not upload real invoices to a public demo.
- The demo backend runs in **mock mode**: every upload returns the same fake invoice. It does not
  run local inference and the UI says so. Your laptop's Ollama is never exposed by the demo.
- Deleting a record from application memory does **not** guarantee permanent erasure. Hosting
  providers, request logs and infrastructure sit outside the app's control.
- If you ever point a hosted backend at a remote model, that is **remote processing**, not local.
  Do not describe it as private or local.

## Sample data

Only synthetic documents belong in the repository. Vendor names, GSTINs and addresses in the samples
are invented. Never commit client invoices, bank details, addresses or unredacted GST information.
