// The dashboard's HTTP wrapper: one place that knows the response envelope and how an error is worded.
//
// Extracted from app.js in v0.5.4. `apiBase` here is a module-scope binding SEEDED ONCE by app.js
// (`setApiBase`) rather than computed at load, which is what makes this module importable -- and
// therefore testable -- in Node. Computing it here would mean calling `resolveApiOrigin()` at load,
// which reads `location`/`localStorage`/`document` and would make this module, and everything importing
// it, as unimportable as app.js.
//
// app.js keeps its own `apiBase` const unchanged for the four places that build a URL directly (download
// links, the shared-file endpoints, the session-mode PATCH). This module does not own that constant; it
// owns the REQUEST.

// EXPORTED AS A LIVE BINDING. Modules extracted from app.js that build a URL directly — a download link,
// a multipart upload — need the base itself, not a wrapped request. An ESM import of a `let` reflects
// later assignments, so `setApiBase` below reaches them, while an importer still cannot assign to it (that
// is a syntax error). One writer, many readers, read-only at every reader: the case live bindings are for.
export let apiBase = '';

/** Seed the base URL. Called once from app.js at startup; every request below is relative to it. */
export function setApiBase(base) {
  apiBase = base;
}

/** The base currently in use. Exported for tests and diagnostics -- nothing in the app reads it. */
export function currentApiBase() {
  return apiBase;
}

export async function api(path, options = {}) {
  const response = await fetch(`${apiBase}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    // FastAPI validation errors return `detail` as an array of {loc,msg,...}; the old
    // `data.detail` coerced that to "[object Object]". Flatten to readable text.
    let detail = data.error || data.detail || response.statusText;
    if (Array.isArray(detail)) detail = detail.map((d) => (d && d.msg) ? d.msg : JSON.stringify(d)).join('; ');
    else if (detail && typeof detail === 'object') detail = JSON.stringify(detail);
    throw new Error(detail);
  }
  return data;
}
