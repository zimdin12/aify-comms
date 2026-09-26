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

// BOUND TO AN ORIGIN (0.7.1). A key is stored under the origin it was entered for and read back only
// for a request to that origin. It was one entry for every origin, so a link carrying
// `?apiOrigin=<another host>` -- which repoints the dashboard and persists, by design -- made the page
// send the operator's key to that host on its first request. A new origin has no key until the
// operator enters one for it, and the prompt names the origin it is asking for.
const STORAGE_PREFIX = 'aify.apiKey@';
// Where every origin's key was kept before 0.7.1. Read once, by `adoptLegacyApiKey`, and removed.
const LEGACY_STORAGE_KEY = 'aify.apiKey';
// A socket is the same service as the http origin it was built from (realtime-socket.mjs).
const HTTP_SCHEME = Object.freeze({ 'http:': 'http:', 'https:': 'https:', 'ws:': 'http:', 'wss:': 'https:' });

/**
 * The http(s) origin a credential for `url` is bound to, or "" when `url` has none (a relative path,
 * junk, another scheme). "" binds nothing: no key is read for it and none can be stored under it.
 */
export function credentialOrigin(url) {
  try {
    const parsed = new URL(String(url));
    const scheme = HTTP_SCHEME[parsed.protocol];
    return scheme && parsed.host ? `${scheme}//${parsed.host}` : '';
  } catch {
    return '';
  }
}

function storageKey(url) {
  const origin = credentialOrigin(url);
  return origin ? `${STORAGE_PREFIX}${origin}` : '';
}

/** The key stored for `url`'s origin, or "" when there is none, storage is unavailable, or it throws. */
export function readApiKey(url) {
  const name = storageKey(url);
  if (!name) return '';
  try {
    return String(globalThis.localStorage?.getItem(name) || '');
  } catch {
    return '';
  }
}

/** True when the key was stored for `url`'s origin. A false return is not an error to report -- see the header. */
export function writeApiKey(value, url) {
  const key = String(value || '').trim();
  const name = storageKey(url);
  if (!key || !name) return false;
  try {
    globalThis.localStorage?.setItem(name, key);
    return true;
  } catch {
    return false;
  }
}

/** Forget `url`'s key. Called when that service rejects it, so a wrong key is not retried for ever. */
export function clearApiKey(url) {
  const name = storageKey(url);
  if (!name) return;
  try {
    globalThis.localStorage?.removeItem(name);
  } catch {
    // Nothing to do: a store that cannot delete cannot have stored anything either.
  }
}

/**
 * Move a key stored before keys were bound to `defaultOrigin` -- the service this page talks to when
 * no override is in force -- and remove the unbound copy. Never to the origin in force: that one may
 * have come from a link, which is the case the binding exists for. A key already stored for
 * `defaultOrigin` wins over the old copy.
 */
export function adoptLegacyApiKey(defaultOrigin) {
  try {
    const legacy = globalThis.localStorage?.getItem(LEGACY_STORAGE_KEY);
    if (legacy == null) return;
    globalThis.localStorage.removeItem(LEGACY_STORAGE_KEY);
    if (!readApiKey(defaultOrigin)) writeApiKey(legacy, defaultOrigin);
  } catch {
    // Storage that throws holds nothing to move.
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
 *
 * The key is stored for `target`, which app.js passes as the page's DEFAULT origin, the service that
 * served it. Only boot calls this. The request carriers adopted for the origin they were about to
 * call, so a `?apiOrigin=` stored by an earlier link received the key of an ordinary `?api_key=`
 * bookmark (v0.7.2, external review item 1).
 */
export function adoptKeyFromLocation(target) {
  if (adopted || !credentialOrigin(target)) return;
  adopted = true;
  try {
    const url = new URL(globalThis.location.href);
    const key = url.searchParams.get('api_key');
    if (!key) return;
    writeApiKey(key, target);
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
 * The header pair to attach to a request for `url`, or null when no key is stored for its origin.
 *
 * Returns the OBJECT rather than mutating a caller's headers, so `api()` keeps its rule that a
 * caller's own headers replace the defaults -- a rule two upload tests already pin.
 */
export function apiKeyHeader(url) {
  const key = readApiKey(url);
  return key ? { 'X-API-Key': key } : null;
}

/**
 * The same credential for a WebSocket, which cannot carry a header at all.
 *
 * The browser WebSocket API takes no headers, so the query parameter is the only carrier a page has
 * (the service also reads its cookie, which a same-origin page would send). Returns the url
 * unchanged when no key is stored for its origin, so an unprotected service is unaffected.
 */
export function withApiKey(url) {
  const key = readApiKey(url);
  if (!key) return url;
  return `${url}${url.includes('?') ? '&' : '?'}api_key=${encodeURIComponent(key)}`;
}
