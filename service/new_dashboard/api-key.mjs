// The service key, as the DASHBOARD holds it: typed once by the operator, kept per-origin, and sent
// as a header on every call.
//
// WHY NOT THE COOKIE THE SERVICE ALREADY ISSUES. Visiting `/?api_key=...` trades the key for an
// HttpOnly cookie, and for a same-origin page that is the better credential. This dashboard is not
// same-origin: `/` on the service port REDIRECTS to the dashboard on 8801, and the page there calls
// the API back on 8800. A cookie does not ride a cross-origin fetch unless the request asks for it
// AND the server allows credentials -- and `main.py` sets `allow_credentials=("*" not in origins)`,
// so with the default `CORS_ORIGINS=*` it deliberately does not. Making the cookie work would mean
// turning credentialed CORS on for every origin, which hands any site the operator visits the
// ability to call this API with the operator's own ambient credential and read the answer. A header
// is not ambient: it is attached only by code running on this origin, so no other page can borrow it.
//
// WHAT THIS COSTS, stated rather than glossed: a key in `localStorage` is readable by script on this
// origin, where an HttpOnly cookie is not. That trade buys the removal of an ambient credential, and
// it is the weaker risk of the two -- script on this origin can already call the API as the operator
// whatever we store.
//
// EVERY ACCESS IS GUARDED. `localStorage` throws outright in some contexts (a browser set to block
// site data, a sandboxed frame), and a throw at module load would take the whole dashboard down
// rather than degrade it. Reads answer "" and writes answer false; the caller then behaves as though
// no key is stored, which is a state the prompt already handles.

const STORAGE_KEY = 'aify.apiKey';

/** The stored key, or "" when there is none, storage is unavailable, or it throws. */
export function readApiKey() {
  try {
    return String(globalThis.localStorage?.getItem(STORAGE_KEY) || '');
  } catch {
    return '';
  }
}

/** True when the key was stored. A false return is not an error to report -- see the header. */
export function writeApiKey(value) {
  const key = String(value || '').trim();
  if (!key) return false;
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, key);
    return true;
  } catch {
    return false;
  }
}

/** Forget the key. Called when the service rejects it, so a wrong key is not retried for ever. */
export function clearApiKey() {
  try {
    globalThis.localStorage?.removeItem(STORAGE_KEY);
  } catch {
    // Nothing to do: a store that cannot delete cannot have stored anything either.
  }
}

// Adoption runs at most once per page. Not a load-time side effect on purpose: this module is
// imported by `api-client`, and a module that touched `location` at load would be unimportable in
// Node -- the property `api-origin.mjs` documents paying for, and the reason its resolver reads
// `location` only when CALLED.
let adopted = false;

/**
 * Take the key out of `?api_key=` if the operator arrived with one, then remove it from the URL.
 *
 * WHY THE DASHBOARD PORT NEEDS ITS OWN COPY OF THIS. The service exchanges `?api_key=` for a cookie,
 * but that only happens on the SERVICE port; the dashboard is served from another one, so an
 * operator opening `http://host:8801/?api_key=...` -- the documented shape, and the one in their
 * bookmarks -- handed the key to a page that did nothing with it and then reported 401s.
 *
 * The parameter is STRIPPED once adopted. Leaving it puts the credential in history, in the address
 * bar, in any bookmark made from the page, and in the `Referer` of every outbound link, which is the
 * leak the typed prompt exists to avoid; adopting it and leaving it there would keep the leak while
 * adding the fix.
 */
export function adoptKeyFromLocation() {
  if (adopted) return;
  adopted = true;
  try {
    const url = new URL(globalThis.location.href);
    const key = url.searchParams.get('api_key');
    if (!key) return;
    writeApiKey(key);
    url.searchParams.delete('api_key');
    globalThis.history.replaceState({}, '', url.toString());
  } catch {
    // No location, no history, or an unparseable href. Nothing to adopt, and nothing to report:
    // the prompt covers the case where no key arrives by any route.
  }
}

/** Exported for tests: adoption is once-per-page, so a test needs to put that back. */
export function resetAdoptionForTests() {
  adopted = false;
}

/**
 * The header pair to attach, or null when there is no key.
 *
 * Returns the OBJECT rather than mutating a caller's headers, so `api()` keeps its rule that a
 * caller's own headers replace the defaults -- a rule two upload tests already pin.
 */
export function apiKeyHeader() {
  adoptKeyFromLocation();
  const key = readApiKey();
  return key ? { 'X-API-Key': key } : null;
}

/**
 * The same credential for a WebSocket, which cannot carry a header at all.
 *
 * The browser WebSocket API takes no headers, so the query parameter is the only carrier a page has
 * (the service also reads its cookie, which a same-origin page would send). Returns the url
 * unchanged when no key is stored, so an unprotected service is unaffected.
 */
export function withApiKey(url) {
  adoptKeyFromLocation();
  const key = readApiKey();
  if (!key) return url;
  return `${url}${url.includes('?') ? '&' : '?'}api_key=${encodeURIComponent(key)}`;
}
