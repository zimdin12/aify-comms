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
import { apiKeyHeader } from './api-key.mjs';
import { ensureApiKeyPrompt } from './api-key-prompt.mjs';

export let apiBase = '';

// The service ROOT, without the `/api/v1` suffix. A live binding for the same reason as `apiBase`, and
// separate from it because not everything the dashboard fetches is under the versioned prefix — `/version`
// is served from the root, so a module that built it from `apiBase` would ask for `/api/v1/version`.
export let apiOrigin = '';

/**
 * Seed both URLs. Called once from app.js at startup; every request below is relative to the base.
 * `origin` defaults to the base so a caller that only knows the one is not left with an empty root.
 */
export function setApiBase(base, origin = base) {
  apiBase = base;
  apiOrigin = origin;
}

/** The base currently in use. Exported for tests and diagnostics -- nothing in the app reads it. */
export function currentApiBase() {
  return apiBase;
}

// The OPERATOR KEY, if this dashboard was served with one. It proves that a request naming
// `requestedBy=operator` really comes from an operator surface — since R5-H1 (2026-08-18) the actor
// string alone grants nothing, because any caller could type it. Never logged, never rendered.
let operatorKey = '';

// Read at module load from what the dashboard server injected into the page. Done HERE rather than
// wired from app.js: app.js is reconstructed byte-identically by `extraction-proof`, so new lines there
// are a gate failure, and the repo's own rule is that new behaviour goes in a module.
if (typeof globalThis !== 'undefined' && globalThis.__AIFY_OPERATOR_KEY__) {
  operatorKey = String(globalThis.__AIFY_OPERATOR_KEY__);
}

/** Seeded once at boot from the value the dashboard server injected. Exported for tests. */
export function setOperatorKey(key) {
  operatorKey = String(key || '');
}

export async function api(path, options = {}) {
  // A CALLER'S HEADERS REPLACE THE DEFAULT — deliberately, and two tests pin it: `headers: {}` is how
  // file upload drops the JSON content-type, and a multipart POST carrying `application/json` does not
  // upload. My first version merged them and broke exactly that; the tests said so.
  //
  // The operator key is attached AFTER, so it survives either shape without changing which
  // content-type a caller ends up with.
  const { headers: callerHeaders, ...rest } = options;
  const headers = callerHeaders ? { ...callerHeaders } : { 'Content-Type': 'application/json' };
  if (operatorKey) headers['X-Aify-Operator-Key'] = operatorKey;
  // THE SERVICE KEY, AND A HEADER RATHER THAN THE COOKIE ON PURPOSE. This page is served from the
  // dashboard port and calls the API back on the service port, so every request here is
  // cross-origin -- and a cookie does not ride a cross-origin fetch unless credentialed CORS is on,
  // which `main.py` switches OFF whenever `CORS_ORIGINS` is `*`. See `api-key.mjs` for why leaving
  // it off is the right call. Attached AFTER the caller's headers for the same reason the operator
  // key is: so it survives a caller that replaced the defaults wholesale.
  const serviceKey = apiKeyHeader();
  if (serviceKey) Object.assign(headers, serviceKey);
  const response = await fetch(`${apiBase}${path}`, { headers, ...rest });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    // A 401 IS ANSWERABLE, so answer it rather than rendering "Invalid or missing API key" into a
    // panel. Before this, a keyed service showed a dashboard that polled, failed and retried with
    // no way for the operator to supply the key except by hand-editing the URL. The prompt mounts
    // once however many requests fail together.
    if (response.status === 401) ensureApiKeyPrompt();
    // FastAPI validation errors return `detail` as an array of {loc,msg,...}; the old
    // `data.detail` coerced that to "[object Object]". Flatten to readable text.
    let detail = data.error || data.detail || response.statusText;
    if (Array.isArray(detail)) detail = detail.map((d) => (d && d.msg) ? d.msg : JSON.stringify(d)).join('; ');
    else if (detail && typeof detail === 'object') detail = JSON.stringify(detail);
    throw new Error(detail);
  }
  return data;
}
