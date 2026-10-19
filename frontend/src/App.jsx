import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  clearDocuments,
  deleteDocument,
  getHealth,
  listDocuments,
  uploadFile,
} from "./api/client.js";
import DocumentQueue from "./components/DocumentQueue.jsx";
import ExportButton from "./components/ExportButton.jsx";
import ExtractionForm from "./components/ExtractionForm.jsx";
import UploadDropzone from "./components/UploadDropzone.jsx";

const ALLOWED = [".jpg", ".jpeg", ".png", ".pdf"];

function describeBackend(health, offline) {
  if (offline) return { tone: "bad", label: "Backend offline" };
  if (!health) return { tone: "neutral", label: "Connecting…" };
  if (health.extraction_mode === "mock") return { tone: "warn", label: "Mock extraction (not real inference)" };
  if (!health.model.reachable) return { tone: "bad", label: "Model offline" };
  if (!health.model.available) return { tone: "bad", label: "Model not installed" };
  if (!health.local_processing) return { tone: "warn", label: "Remote model (not local)" };
  return { tone: "good", label: `Local processing · ${health.model.name}` };
}

export default function App() {
  const [health, setHealth] = useState(null);
  const [offline, setOffline] = useState(false);
  const [loading, setLoading] = useState(true);
  const [documents, setDocuments] = useState([]);
  const [uploads, setUploads] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [notice, setNotice] = useState(null); // {tone, text}
  const uploadSeq = useRef(0);

  const refreshHealth = useCallback(async () => {
    try {
      setHealth(await getHealth());
      setOffline(false);
      return true;
    } catch {
      setOffline(true);
      return false;
    }
  }, []);

  useEffect(() => {
    (async () => {
      if (await refreshHealth()) {
        try {
          setDocuments(await listDocuments());
        } catch (error) {
          setNotice({ tone: "bad", text: error.message });
        }
      }
      setLoading(false);
    })();
  }, [refreshHealth]);

  const patchUpload = (id, changes) =>
    setUploads((current) => current.map((item) => (item.id === id ? { ...item, ...changes } : item)));

  async function handleFiles(files) {
    setNotice(null);
    const limits = health?.limits;
    const batch = files.map((file) => ({
      id: `upload-${++uploadSeq.current}`,
      name: file.name,
      status: "queued",
      message: "",
    }));
    setUploads((current) => [...current, ...batch]);

    let added = 0;
    let failed = 0;
    let firstNewId = null;

    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      const item = batch[index];
      const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();

      // Quick client-side checks for fast feedback. The server remains the authority.
      if (!ALLOWED.includes(extension)) {
        patchUpload(item.id, { status: "error", message: "Only JPG, PNG and PDF files are supported." });
        failed += 1;
        continue;
      }
      if (limits && file.size > limits.max_upload_mb * 1024 * 1024) {
        patchUpload(item.id, { status: "error", message: `Larger than the ${limits.max_upload_mb} MB limit.` });
        failed += 1;
        continue;
      }

      patchUpload(item.id, { status: "processing" });
      try {
        const body = await uploadFile(file);
        if (body.documents.length > 0) {
          setDocuments((current) => [...current, ...body.documents]);
          firstNewId = firstNewId ?? body.documents[0].document_id;
          added += 1;
          setUploads((current) => current.filter((entry) => entry.id !== item.id));
        } else {
          const first = body.errors[0];
          patchUpload(item.id, { status: "error", message: first ? first.message : "Nothing was extracted." });
          failed += 1;
        }
      } catch (error) {
        patchUpload(item.id, { status: "error", message: error.message });
        failed += 1;
        if (error.code === "offline") setOffline(true);
      }
    }

    if (firstNewId) setSelectedId(firstNewId);
    if (added > 0 && failed === 0) {
      setNotice({ tone: "good", text: `${added} document${added === 1 ? "" : "s"} extracted. Review each one before exporting.` });
    } else if (added > 0) {
      setNotice({ tone: "warn", text: `${added} extracted, ${failed} failed. See the queue for details.` });
    } else if (failed > 0) {
      setNotice({ tone: "bad", text: `No documents were extracted (${failed} failed).` });
    }
    refreshHealth();
  }

  async function handleDelete(id) {
    try {
      await deleteDocument(id);
      setDocuments((current) => current.filter((doc) => doc.document_id !== id));
      setSelectedId((current) => (current === id ? null : current));
    } catch (error) {
      setNotice({ tone: "bad", text: error.message });
    }
  }

  async function handleClear() {
    if (!window.confirm("Delete every record in this batch? This cannot be undone.")) return;
    try {
      await clearDocuments();
      setDocuments([]);
      setUploads([]);
      setSelectedId(null);
      setNotice({ tone: "good", text: "The batch was deleted." });
    } catch (error) {
      setNotice({ tone: "bad", text: error.message });
    }
  }

  const selected = documents.find((doc) => doc.document_id === selectedId) || null;
  const reviewedCount = useMemo(() => documents.filter((doc) => doc.reviewed).length, [documents]);
  const backend = describeBackend(health, offline);
  const modelProblem = !offline && health && health.extraction_mode === "gemma" && !health.model.available;
  const isEmpty = !loading && documents.length === 0 && uploads.length === 0;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <h1>InvoiceLens</h1>
          <p className="muted">Your invoices stay yours.</p>
        </div>
        <p className={`status status-${backend.tone}`} role="status">
          <span className="dot" aria-hidden="true" /> {backend.label}
        </p>
      </header>

      <main>
        <p className="banner banner-warn" role="note">
          <strong>Review every field.</strong> Extraction can be wrong. Compare each value with the original
          document before you rely on it.
        </p>

        {offline && (
          <div className="banner banner-bad" role="alert">
            <strong>Cannot reach the backend.</strong> Start it with{" "}
            <code>uvicorn app.main:app --reload</code> from the <code>backend</code> folder.{" "}
            <button type="button" className="btn btn-link" onClick={() => refreshHealth()}>
              Try again
            </button>
          </div>
        )}
        {modelProblem && (
          <div className="banner banner-bad" role="alert">
            <strong>The model is not ready.</strong> {health.model.detail}{" "}
            <button type="button" className="btn btn-link" onClick={() => refreshHealth()}>
              Check again
            </button>
          </div>
        )}
        {health?.extraction_mode === "mock" && (
          <p className="banner banner-warn" role="note">
            Mock mode returns the same fake invoice for every upload. It exists for development and demos
            only.
          </p>
        )}
        {health && !offline && !health.ocr.available && (
          <p className="banner banner-info" role="note">
            Tesseract is not installed, so the OCR fallback is unavailable. Extraction still works when the
            model reads the image directly.
          </p>
        )}
        {notice && (
          <p className={`banner banner-${notice.tone}`} role="status">
            {notice.text}
          </p>
        )}

        <div className="layout">
          <div className="column">
            <UploadDropzone onFiles={handleFiles} disabled={offline} limits={health?.limits} />

            <section className="panel">
              <header className="panel-head">
                <h2>Documents</h2>
                {documents.length > 0 && (
                  <button type="button" className="btn btn-link" onClick={handleClear}>
                    Delete batch
                  </button>
                )}
              </header>
              {loading && <p className="muted" role="status">Loading…</p>}
              {isEmpty && (
                <p className="empty">
                  No documents yet. Upload a synthetic or your own invoice to get started. Records are kept in
                  memory only and are lost when the server restarts.
                </p>
              )}
              {(documents.length > 0 || uploads.length > 0) && (
                <DocumentQueue
                  uploads={uploads}
                  documents={documents}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                  onDismissUpload={(id) => setUploads((current) => current.filter((item) => item.id !== id))}
                />
              )}
            </section>

            <section className="panel">
              <h2>Export</h2>
              <ExportButton total={documents.length} reviewed={reviewedCount} />
            </section>
          </div>

          <div className="column">
            {selected ? (
              <ExtractionForm
                key={selected.document_id}
                record={selected}
                onSaved={(saved) =>
                  setDocuments((current) => current.map((doc) => (doc.document_id === saved.document_id ? saved : doc)))
                }
                onDelete={handleDelete}
              />
            ) : (
              <section className="panel placeholder">
                <h2>Review</h2>
                <p className="muted">Select a document to check and correct its extracted fields.</p>
              </section>
            )}
          </div>
        </div>
      </main>

      <footer className="footer muted small">
        {health?.local_processing
          ? "Local processing: documents are handled on this machine and kept in memory only."
          : "Check where the model runs before uploading real documents. Use synthetic samples for demos."}
      </footer>
    </div>
  );
}
