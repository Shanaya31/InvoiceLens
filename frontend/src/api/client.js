// Thin wrapper around the InvoiceLens backend. Every function either resolves with data or
// throws an ApiError whose message is safe to show to the user.

const BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const SESSION_STORAGE_KEY = "invoicelens_session_id";

export class ApiError extends Error {
  constructor(message, { status = 0, code = "error", fieldErrors = {} } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fieldErrors = fieldErrors;
  }
}

function generateSessionId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40; // RFC 4122 v4
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  throw new ApiError(
    "Secure random number generation (Web Crypto API) is required for session isolation.",
    { code: "session_unavailable" }
  );
}

export function getSessionId() {
  if (typeof window === "undefined") {
    throw new ApiError(
      "Browser window environment is required for session isolation.",
      { code: "session_unavailable" }
    );
  }

  let storage = null;
  try {
    storage = window.sessionStorage;
  } catch {
    storage = null;
  }

  if (!storage) {
    throw new ApiError(
      "Browser session storage is unavailable or restricted. Enable session storage to ensure document isolation.",
      { code: "session_unavailable" }
    );
  }

  try {
    let id = storage.getItem(SESSION_STORAGE_KEY);

    const validSessionId =
      typeof id === "string" &&
      /^[a-zA-Z0-9_-]{8,128}$/.test(id.trim());

    if (!validSessionId) {
      id = generateSessionId();
      storage.setItem(SESSION_STORAGE_KEY, id);
    } else {
      id = id.trim();
    }

    return id;
  } catch {
    throw new ApiError(
      "Failed to read or write session storage for document isolation.",
      { code: "session_unavailable" }
    );
  }
}

async function request(path, options = {}) {
  const sessionId = getSessionId();
  const headers = {
    ...(options.headers || {}),
    "X-Session-ID": sessionId,
  };
  try {
    return await fetch(`${BASE}${path}`, { ...options, headers });
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError("Cannot reach the InvoiceLens server. Is the backend running?", {
      code: "offline",
    });
  }
}

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function toApiError(response, body) {
  const fieldErrors = {};
  let message = `Request failed (HTTP ${response.status}).`;
  if (body && typeof body.detail === "string") {
    message = body.detail;
  } else if (body && Array.isArray(body.detail)) {
    for (const item of body.detail) {
      const field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : null;
      const text = String(item.msg || "Invalid value").replace(/^Value error, /, "");
      if (typeof field === "string") fieldErrors[field] = text;
    }
    message = "Some values could not be saved. Check the highlighted fields.";
  }
  return new ApiError(message, { status: response.status, fieldErrors });
}

async function jsonOrThrow(response) {
  const body = await readJson(response);
  if (!response.ok) throw toApiError(response, body);
  return body;
}

export async function getHealth() {
  return jsonOrThrow(await request("/api/health"));
}

export async function listDocuments() {
  return jsonOrThrow(await request("/api/documents"));
}

/** Upload one file. Resolves with {documents, errors}; per-file failures are in `errors`. */
export async function uploadFile(file) {
  const form = new FormData();
  form.append("files", file);
  const response = await request("/api/documents", { method: "POST", body: form });
  const body = await readJson(response);
  // The server answers 200/422/503 with this shape; anything else is a request-level error.
  if (body && Array.isArray(body.documents) && Array.isArray(body.errors)) return body;
  throw toApiError(response, body);
}

export async function updateDocument(id, changes) {
  const response = await request(`/api/documents/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  return jsonOrThrow(response);
}

export async function deleteDocument(id) {
  const response = await request(`/api/documents/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!response.ok && response.status !== 404) throw toApiError(response, await readJson(response));
}

export async function clearDocuments() {
  const response = await request("/api/documents", { method: "DELETE" });
  if (!response.ok) throw toApiError(response, await readJson(response));
}

/** Download the CSV of current (corrected) records and hand it to the browser. */
export async function downloadCsv({ reviewedOnly = false } = {}) {
  const query = reviewedOnly ? "?reviewed_only=true" : "";
  const response = await request(`/api/export.csv${query}`);
  if (!response.ok) throw toApiError(response, await readJson(response));
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "invoicelens_export.csv";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
