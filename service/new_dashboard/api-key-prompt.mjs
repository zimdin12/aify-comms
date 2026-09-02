// "This dashboard needs the key" -- the small login the operator asked for, instead of a URL they
// have to hand-edit.
//
// WHAT IT REPLACES. With `API_KEY` set, the only documented way in was to visit
// `<endpoint>/?api_key=<value>` in the address bar. That works, and then the credential is in
// browser history, in the address bar, in any bookmark made from that page, and in the `Referer` of
// every outbound link. Typing it into a field puts it in none of those.
//
// IT MOUNTS ITSELF ONCE. `ensureApiKeyPrompt` is called from the 401 path of every request, so a
// dashboard doing six polls a second would otherwise stack six overlays; the mounted node is its own
// guard. It takes `doc` rather than reaching for the global so the whole thing can be driven by a
// fake document in a test -- the alternative is a module only a browser can execute, which is how a
// login form goes untested until an operator finds it broken.

import { writeApiKey, clearApiKey } from './api-key.mjs';

export const PROMPT_ID = 'aify-api-key-prompt';

/** True when the prompt is already on the page. Exported because it is the whole idempotence rule. */
export function isMounted(doc) {
  return Boolean(doc && doc.getElementById && doc.getElementById(PROMPT_ID));
}

/**
 * Put the prompt on the page unless it is already there.
 *
 * `onAccepted` runs after a key is stored. The default reloads, which is the honest thing: the
 * dashboard has already rendered an unknown number of failed panels, and re-fetching just the one
 * request that happened to 401 would leave the rest empty with no way to tell.
 */
export function ensureApiKeyPrompt(doc = globalThis.document, onAccepted = defaultAccept) {
  if (!doc || !doc.createElement || isMounted(doc)) return null;

  // A key that is present and REFUSED must not survive, or the prompt re-appears on every load
  // pre-filled with the value the service just rejected, and the operator cannot tell that their
  // typing did anything.
  clearApiKey();

  const overlay = doc.createElement('div');
  overlay.id = PROMPT_ID;
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-label', 'API key required');
  overlay.style.cssText = [
    'position:fixed', 'inset:0', 'z-index:99999',
    'display:flex', 'align-items:center', 'justify-content:center',
    'background:rgba(10,12,16,0.82)',
    'font:14px system-ui,-apple-system,Segoe UI,sans-serif',
  ].join(';');

  const card = doc.createElement('form');
  card.style.cssText = [
    'background:#171a21', 'color:#e6e8ec', 'padding:24px', 'border-radius:10px',
    'min-width:320px', 'max-width:90vw', 'box-shadow:0 12px 40px rgba(0,0,0,0.5)',
    'display:flex', 'flex-direction:column', 'gap:12px',
  ].join(';');

  const title = doc.createElement('h2');
  title.textContent = 'This service needs a key';
  title.style.cssText = 'margin:0;font-size:16px;font-weight:600';

  const hint = doc.createElement('p');
  hint.textContent = 'Enter the API key for this aify-comms service. It is stored in this browser only.';
  hint.style.cssText = 'margin:0;font-size:13px;opacity:0.75;line-height:1.4';

  const input = doc.createElement('input');
  input.type = 'password';
  input.autocomplete = 'current-password';
  input.setAttribute('aria-label', 'API key');
  input.placeholder = 'API key';
  input.style.cssText = [
    'padding:9px 11px', 'border-radius:6px', 'border:1px solid #333a45',
    'background:#0f1116', 'color:#e6e8ec', 'font:inherit',
  ].join(';');

  const button = doc.createElement('button');
  button.type = 'submit';
  button.textContent = 'Connect';
  button.style.cssText = [
    'padding:9px 11px', 'border-radius:6px', 'border:0',
    'background:#3b82f6', 'color:#fff', 'font:inherit', 'font-weight:600', 'cursor:pointer',
  ].join(';');

  card.appendChild(title);
  card.appendChild(hint);
  card.appendChild(input);
  card.appendChild(button);
  overlay.appendChild(card);

  card.addEventListener('submit', (event) => {
    // Without this the form navigates and the typed key lands in the URL -- reintroducing, through
    // the fix, the exact leak the fix exists to remove.
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    if (writeApiKey(input.value)) onAccepted();
  });

  doc.body.appendChild(overlay);
  // Focus last: an element must be in the document to take it. Guarded because a fake document in a
  // test has no focus, and a login form is not worth a crash.
  try { input.focus(); } catch { /* not focusable here */ }
  return overlay;
}

function defaultAccept() {
  try { globalThis.location.reload(); } catch { /* no location to reload */ }
}
