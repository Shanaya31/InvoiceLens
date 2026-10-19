import { useRef, useState } from "react";

const ACCEPT = ".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf";

export default function UploadDropzone({ onFiles, disabled, limits }) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);

  function submit(fileList) {
    const files = Array.from(fileList || []);
    if (files.length > 0 && !disabled) onFiles(files);
    if (inputRef.current) inputRef.current.value = ""; // allow re-selecting the same file
  }

  function onDrop(event) {
    event.preventDefault();
    setDragging(false);
    submit(event.dataTransfer.files);
  }

  const maxMb = limits?.max_upload_mb ?? 10;
  const maxPages = limits?.max_pdf_pages ?? 5;

  return (
    <section
      className={`dropzone${dragging ? " is-dragging" : ""}${disabled ? " is-disabled" : ""}`}
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      aria-label="Upload invoices and receipts"
    >
      <p className="dropzone-title">Drop invoices or receipts here</p>
      <p className="muted">
        JPG, PNG or PDF · up to {maxMb} MB each · PDFs up to {maxPages} pages
      </p>
      <input
        ref={inputRef}
        id="file-input"
        className="visually-hidden"
        type="file"
        accept={ACCEPT}
        multiple
        disabled={disabled}
        onChange={(event) => submit(event.target.files)}
      />
      <button
        type="button"
        className="btn btn-primary"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
      >
        Browse files
      </button>
      {disabled && (
        <p className="muted small" role="status">
          Uploads are paused until the backend is reachable.
        </p>
      )}
    </section>
  );
}
