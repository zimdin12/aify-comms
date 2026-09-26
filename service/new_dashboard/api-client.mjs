// The dashboard's HTTP wrapper: one place that knows the response envelope and how an error is worded.
//
// Extracted from app.js in v0.5.4. `apiBase` here is a module-scope binding SEEDED ONCE by app.js
// (`setApiBase`) rather than computed at load, which is what makes this module importable -- and
// therefore testable -- in Node. Computing it here would mean calling `resolveApiOrigin()` at load,
// which reads `location`/`localStorage`/`document` and would make this module, and everything importing
// it, as unimportable as app.js.
//
// app.js keeps its own `apiBase` const. Direct download links need the URL; mutations use
// this module, which owns authentication for both parsed data and raw Response callers.

// EXPORTED AS A LIVE BINDING. Modules extracted from app.js that build a URL directly — a download link,
// a multipart upload — need the base itself, not a wrapped request. An ESM import of a `let` reflects
// later assignments, so `setApiBase` below reaches them, while an importer still cannot assign to it (that
// is a syntax error). One writer, many readers, read-only at every reader: the case live bindings are for.
import { apiKeyHeader, credentialOrigin } from './api-key.mjs';
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

// The OPERATOR KEY, if this dashboard was served with one. It proves that a request naming
// `requestedBy=operator` really comes from an operator surface — since R5-H1 (2026-08-18) the actor
// string alone grants nothing, because any caller could type it. Never logged, never rendered.
//
// BOUND TO ONE ORIGIN: the service that served this page, which app.js names at boot. It was sent on
// every request whatever the destination, so a link with `?apiOrigin=https://receiver` handed it to the
// receiver as the page loaded (v0.7.1 review, W03-R1). Bound to nothing, it is sent nowhere.
let operatorKey = '';
let operatorKeyOrigin = '';

// Read at module load from what the dashboard server injected into the page. Done HERE rather than
// wired from app.js, because the repo's rule is that new behaviour goes in a module.
if (typeof globalThis !== 'undefined' && globalThis.__AIFY_OPERATOR_KEY__) {
  operatorKey = String(globalThis.__AIFY_OPERATOR_KEY__);
}

/** Set the key and the one origin it may go to. Exported for tests; app.js uses `bindOperatorKeyTo`. */
export function setOperatorKey(key, origin = operatorKeyOrigin) {
  operatorKey = String(key || '');
  operatorKeyOrigin = credentialOrigin(origin);
}

/** Name the origin the injected key belongs to: the service that served this page. Called once at boot. */
export function bindOperatorKeyTo(origin) {
  operatorKeyOrigin = credentialOrigin(origin);
}

// Return the untouched Response for callers with status-specific workflows (409 consent).
// Authentication and the 401 prompt still have exactly one owner.
export async function apiResponse(path, options = {}) {
  // A CALLER'S HEADERS REPLACE THE DEFAULT — deliberately, and two tests pin it: `headers: {}` is how
  // file upload drops the JSON content-type, and a multipart POST carrying `application/json` does not
  // upload. My first version merged them and broke exactly that; the tests said so.
  //
  // The operator key is attached AFTER, so it survives either shape without changing which
  // content-type a caller ends up with.
  const { headers: callerHeaders, ...rest } = options;
  const headers = callerHeaders ? { ...callerHeaders } : { 'Content-Type': 'application/json' };
  const url = `${apiBase}${path}`;
  if (operatorKey && operatorKeyOrigin && credentialOrigin(url) === operatorKeyOrigin) {
    headers['X-Aify-Operator-Key'] = operatorKey;
  }
  // THE SERVICE KEY, AND A HEADER RATHER THAN THE COOKIE ON PURPOSE. This page is served from the
  // dashboard port and calls the API back on the service port, so every request here is
  // cross-origin -- and a cookie does not ride a cross-origin fetch unless credentialed CORS is on,
  // which `main.py` switches OFF whenever `CORS_ORIGINS` is `*`. See `api-key.mjs` for why leaving
  // it off is the right call. Attached AFTER the caller's headers for the same reason the operator
  // key is: so it survives a caller that replaced the defaults wholesale. The key is looked up for
  // the URL the request goes to, so it can only ever reach the origin it was entered for.
  const serviceKey = apiKeyHeader(url);
  if (serviceKey) Object.assign(headers, serviceKey);
  // NEVER FOLLOWED, and last so no caller's options can turn it back on: fetch re-sends custom headers
  // on a redirect, so a proxy answering 302 would collect the operator key and the service key for
  // whatever origin it names (v0.7.1 review, W03-R2). No dashboard API call relies on a redirect.
  const response = await fetch(url, { headers, ...rest, redirect: 'error' });
  if (response.status === 401) ensureApiKeyPrompt(url);
  return response;
}

export async function api(path, options = {}) {
  const response = await apiResponse(path, options);
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
