import { useState } from "react";
import { downloadCsv } from "../api/client.js";

export default function ExportButton({ total, reviewed }) {
  const [reviewedOnly, setReviewedOnly] = useState(false);
  const [state, setState] = useState({ status: "idle", message: "" });

  const count = reviewedOnly ? reviewed : total;
  const disabled = count === 0 || state.status === "working";

  async function onClick() {
    setState({ status: "working", message: "" });
    try {
      await downloadCsv({ reviewedOnly });
      setState({ status: "ok", message: `Exported ${count} record${count === 1 ? "" : "s"}.` });
    } catch (error) {
      setState({ status: "error", message: error.message });
    }
  }

  return (
    <div className="export">
      <button type="button" className="btn btn-primary" onClick={onClick} disabled={disabled}>
        {state.status === "working" ? "Exporting…" : "Export CSV"}
      </button>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={reviewedOnly}
          onChange={(event) => setReviewedOnly(event.target.checked)}
        />
        Reviewed records only ({reviewed})
      </label>
      <p className="muted small" role="status">
        {state.status === "ok" && state.message}
        {state.status === "error" && <span className="text-error">{state.message}</span>}
        {state.status === "idle" && total > reviewed && "Exports include your corrections. Unreviewed records are marked in the file."}
      </p>
    </div>
  );
}
