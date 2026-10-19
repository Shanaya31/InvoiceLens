import { useEffect, useState } from "react";
import { updateDocument } from "../api/client.js";
import ReviewFlags from "./ReviewFlags.jsx";

const TEXT_FIELDS = [
  ["vendor_name", "Vendor name"],
  ["vendor_gstin", "Vendor GSTIN"],
  ["invoice_number", "Invoice number"],
  ["invoice_date", "Invoice date", "date"],
  ["expense_category", "Expense category"],
];

const MONEY_FIELDS = [
  ["subtotal", "Subtotal"],
  ["discount_amount", "Discount"],
  ["cgst_amount", "CGST"],
  ["sgst_amount", "SGST"],
  ["igst_amount", "IGST"],
  ["other_tax_amount", "Other tax"],
  ["total_tax_amount", "Total tax"],
  ["total_amount", "Total amount"],
];

const EDITABLE = ["document_type", ...TEXT_FIELDS.map((f) => f[0]), ...MONEY_FIELDS.map((f) => f[0])];

function toDraft(record) {
  const draft = {};
  for (const key of EDITABLE) draft[key] = record[key] ?? "";
  return draft;
}

function Field({ name, label, type = "text", draft, setDraft, record, errors, money }) {
  const needsCheck = record.field_review?.[name];
  const error = errors[name];
  const hintId = `${name}-hint`;
  return (
    <div className={`field${needsCheck ? " needs-check" : ""}${error ? " has-error" : ""}`}>
      <label htmlFor={`f-${name}`}>
        {label}
        {needsCheck && <span className="check-tag">Check</span>}
      </label>
      <input
        id={`f-${name}`}
        name={name}
        type={type}
        inputMode={money ? "decimal" : undefined}
        autoComplete="off"
        spellCheck="false"
        value={draft[name]}
        aria-invalid={error ? "true" : undefined}
        aria-describedby={error ? hintId : undefined}
        onChange={(event) => setDraft((current) => ({ ...current, [name]: event.target.value }))}
      />
      {error && (
        <p id={hintId} className="text-error small" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export default function ExtractionForm({ record, onSaved, onDelete }) {
  const [draft, setDraft] = useState(() => toDraft(record));
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState({});
  const [message, setMessage] = useState({ kind: "", text: "" });

  // Re-sync with the server's normalised values (for example "12000" -> "12000.00") after a save.
  useEffect(() => setDraft(toDraft(record)), [record]);

  async function onSubmit(event) {
    event.preventDefault();
    const original = toDraft(record);
    const changes = {};
    for (const key of EDITABLE) {
      if (draft[key] !== original[key]) {
        if (key === "document_type") changes[key] = draft[key];
        else changes[key] = draft[key].trim() === "" ? null : draft[key].trim();
      }
    }
    setSaving(true);
    setErrors({});
    setMessage({ kind: "", text: "" });
    try {
      const saved = await updateDocument(record.document_id, { ...changes, reviewed: true });
      onSaved(saved);
      setMessage({ kind: "ok", text: "Saved. This record is marked as reviewed." });
    } catch (error) {
      setErrors(error.fieldErrors || {});
      setMessage({ kind: "error", text: error.message });
    } finally {
      setSaving(false);
    }
  }

  const shared = { draft, setDraft, record, errors };
  return (
    <form className="panel review" onSubmit={onSubmit} noValidate>
      <header className="panel-head">
        <div>
          <h2>{record.filename}</h2>
          <p className="muted small">
            {record.reviewed ? "Reviewed" : "Not reviewed yet"}
            {record.corrected_fields.length > 0 && ` · ${record.corrected_fields.length} field(s) corrected`}
          </p>
        </div>
      </header>

      <ReviewFlags flags={record.review_flags} method={record.extraction_method} validation={record.validation} />

      <div className="grid">
        <div className="field">
          <label htmlFor="f-document_type">Document type</label>
          <select
            id="f-document_type"
            value={draft.document_type}
            onChange={(event) => setDraft((current) => ({ ...current, document_type: event.target.value }))}
          >
            <option value="invoice">Invoice</option>
            <option value="receipt">Receipt</option>
            <option value="unknown">Unknown</option>
          </select>
        </div>
        {TEXT_FIELDS.map(([name, label, type]) => (
          <Field key={name} name={name} label={label} type={type} {...shared} />
        ))}
      </div>

      <fieldset className="money">
        <legend>Amounts ({record.currency})</legend>
        <div className="grid">
          {MONEY_FIELDS.map(([name, label]) => (
            <Field key={name} name={name} label={label} money {...shared} />
          ))}
        </div>
      </fieldset>

      <div className="actions">
        <button type="submit" className="btn btn-primary" disabled={saving}>
          {saving ? "Saving…" : "Save & mark reviewed"}
        </button>
        <button
          type="button"
          className="btn btn-danger"
          disabled={saving}
          onClick={() => onDelete(record.document_id)}
        >
          Delete
        </button>
        <p
          className={message.kind === "error" ? "text-error small" : "text-ok small"}
          role={message.kind === "error" ? "alert" : "status"}
        >
          {message.text}
        </p>
      </div>
    </form>
  );
}
