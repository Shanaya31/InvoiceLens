// Plain-language explanations for the flag codes produced by the backend.
const FLAG_TEXT = {
  document_type_unknown: ["warn", "The document type could not be determined."],
  vendor_name_missing: ["warn", "Vendor name is missing."],
  vendor_gstin_missing: ["info", "No vendor GSTIN, but GST is charged. Add it if it is printed on the document."],
  vendor_gstin_invalid_format: ["warn", "The GSTIN is not in a valid format. (A valid format does not prove registration.)"],
  invoice_number_missing: ["warn", "Invoice number is missing."],
  invoice_date_missing: ["warn", "Invoice date is missing or was ambiguous."],
  invoice_date_ambiguous: ["warn", "The date could be read more than one way, so it was left empty. Enter it as YYYY-MM-DD."],
  invoice_date_in_future: ["warn", "The invoice date is in the future."],
  total_amount_missing: ["warn", "Total amount is missing."],
  total_not_positive: ["warn", "The total is zero or negative."],
  total_mismatch: ["warn", "Subtotal, discount and tax do not add up to the total (beyond the rounding tolerance)."],
  tax_total_mismatch: ["warn", "The individual tax amounts do not add up to the total tax."],
  mixed_gst_tax_types: ["warn", "Both IGST and CGST/SGST are charged. Intra-state and inter-state tax are not normally combined."],
  cgst_sgst_unequal: ["info", "CGST and SGST differ; they are normally equal."],
  model_timeout: ["info", "The model timed out on the first attempt."],
  model_response_invalid: ["info", "The model's first answer was not usable."],
  ocr_unavailable: ["warn", "OCR fallback was needed but Tesseract is not installed."],
  ocr_failed: ["warn", "OCR fallback was needed but failed."],
  ocr_no_text: ["info", "OCR found no readable text."],
  ocr_retry_failed: ["info", "The OCR-assisted retry did not produce a usable answer."],
  mock_extraction: ["warn", "MOCK DATA: this is a fixed synthetic example, not real extraction."],
};

function describe(flag) {
  if (FLAG_TEXT[flag]) return FLAG_TEXT[flag];
  if (flag.endsWith("_unreadable")) {
    return ["warn", `${flag.replace(/_unreadable$/, "").replace(/_/g, " ")} could not be read, so it was left empty.`];
  }
  return ["info", flag.replace(/_/g, " ")];
}

const METHOD_TEXT = {
  gemma_vision: "Read directly from the image by the model",
  ocr_gemma: "Read with OCR text, then the model (fallback)",
  mock: "Mock data (no real extraction)",
};

export default function ReviewFlags({ flags, method, validation }) {
  return (
    <section className="flags" aria-label="Validation warnings">
      <h3>Checks</h3>
      <p className="muted small">
        {METHOD_TEXT[method] || method}
        {validation?.arithmetic_valid === true && " · Arithmetic checks out"}
        {validation?.arithmetic_valid === false && " · Arithmetic does not match"}
        {validation?.arithmetic_valid === null && " · Not enough amounts to check arithmetic"}
      </p>
      {flags.length === 0 ? (
        <p className="flag flag-ok">No warnings. Still compare each field with the original document.</p>
      ) : (
        <ul className="flag-list">
          {flags.map((flag) => {
            const [level, text] = describe(flag);
            return (
              <li key={flag} className={`flag flag-${level}`}>
                {text}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
