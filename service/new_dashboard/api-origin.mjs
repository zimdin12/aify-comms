// Where the dashboard decides which server it talks to.
//
// Extracted from app.js in v0.5.4. This is the resolver ONLY — `apiOrigin` and `apiBase` are still
// computed in app.js, because they are evaluated at module load and a module that ran this function at
// load would be unimportable in Node, which is exactly the property this extraction is buying. The
// function itself touches `location`, `localStorage` and `document` only when CALLED, so this module
// imports cleanly and can be tested. See docs/APP_JS_APIBASE_PACKET.md for the open question about the
// two constants.

export function resolveApiOrigin() {
  const params = new URLSearchParams(location.search);
  const requested = params.get('apiOrigin');
  if (requested) {
    localStorage.setItem('aify.next.apiOrigin', requested.replace(/\/+$/, ''));
    return requested.replace(/\/+$/, '');
  }
  const stored = localStorage.getItem('aify.next.apiOrigin');
  if (stored) return stored.replace(/\/+$/, '');
  const port = document.documentElement.dataset.defaultApiPort || '8800';
  return `${location.protocol}//${location.hostname}:${port}`;
}
