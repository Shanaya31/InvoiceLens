function formatTotal(record) {
  if (record.total_amount == null) return "—";
  const number = Number(record.total_amount);
  if (!Number.isFinite(number)) return record.total_amount;
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: record.currency || "INR" }).format(number);
}

function StatusBadge({ record }) {
  if (record.reviewed) return <span className="badge badge-ok">Reviewed</span>;
  const count = record.review_flags.length;
  if (count > 0) {
    return <span className="badge badge-warn">{count} warning{count === 1 ? "" : "s"}</span>;
  }
  return <span className="badge badge-neutral">Ready to review</span>;
}

export default function DocumentQueue({ uploads, documents, selectedId, onSelect, onDismissUpload }) {
  return (
    <div className="table-wrap">
      <table className="queue">
        <caption className="visually-hidden">Uploaded documents</caption>
        <thead>
          <tr>
            <th scope="col">File</th>
            <th scope="col">Type</th>
            <th scope="col">Vendor</th>
            <th scope="col" className="num">Total</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {uploads.map((item) => (
            <tr key={item.id} className={item.status === "error" ? "row-error" : "row-pending"}>
              <th scope="row" className="file-cell">{item.name}</th>
              <td colSpan={3} className="muted">
                {item.status === "queued" && "Waiting…"}
                {item.status === "processing" && "Extracting with the local model…"}
                {item.status === "error" && <span className="text-error">{item.message}</span>}
              </td>
              <td>
                {item.status === "error" ? (
                  <button type="button" className="btn btn-link" onClick={() => onDismissUpload(item.id)}>
                    Dismiss
                  </button>
                ) : (
                  <span className="badge badge-neutral">
                    <span className="spinner" aria-hidden="true" /> {item.status === "queued" ? "Queued" : "Processing"}
                  </span>
                )}
              </td>
            </tr>
          ))}
          {documents.map((record) => {
            const selected = record.document_id === selectedId;
            return (
              <tr key={record.document_id} className={selected ? "is-selected" : ""}>
                <th scope="row" className="file-cell">
                  <button
                    type="button"
                    className="row-button"
                    aria-pressed={selected}
                    onClick={() => onSelect(record.document_id)}
                  >
                    {record.filename}
                  </button>
                </th>
                <td className="cap">{record.document_type}</td>
                <td>{record.vendor_name || <span className="muted">—</span>}</td>
                <td className="num">{formatTotal(record)}</td>
                <td><StatusBadge record={record} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
