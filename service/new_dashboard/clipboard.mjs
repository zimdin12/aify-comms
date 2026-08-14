// Copying to the clipboard: the two-path write, and copying the live console.
//
// `copyText` tries `navigator.clipboard` first and falls back to the off-screen-textarea + `execCommand`
// hack, because the async clipboard API is unavailable outside a secure context — and the dashboard is
// routinely opened over plain HTTP on a LAN address, which is exactly that case. Neither path is allowed
// to throw: a copy button that raises is worse than one that reports failure.
//
// `copyActiveConsole` copies the operator's selection if there is one and otherwise selects the whole
// buffer, copies that, and CLEARS the selection again — so copy-all does not leave the console visually
// highlighted afterwards. That undo is the part worth a test; it is invisible in the source and obvious
// on screen.
//
// Extracted from app.js in v0.5.4 as a measured closure needing only `state` and `toast`.
//
// The declarations are byte-identical to those that stood in app.js; the only substitution is the added
// `export `, which the reconstruction proof strips before comparing. Their leading comments stayed behind
// in app.js deliberately — `declarationSpan` returns the declaration alone, so a span that took its
// comments could not round-trip through the proof.


import { state } from './state.mjs';
import { toast } from './ui.js';

export async function copyText(text) {
  if (!text) return false;
  try {
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
  } catch { /* fall through */ }
  try {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch { return false; }
}
export function copyActiveConsole() {
  const entry = state.activeXterm;
  if (!entry || !entry.term) return;
  let text = '';
  let autoSelected = false;
  try {
    if (entry.term.hasSelection()) { text = entry.term.getSelection(); }
    else { entry.term.selectAll(); autoSelected = true; text = entry.term.getSelection(); }
  } catch {}
  // Don't leave the whole buffer visually selected when we auto-selected to copy-all.
  if (autoSelected) { try { entry.term.clearSelection(); } catch {} }
  // `.catch` as well as `.then`: `copyText` RESOLVES false on a refused clipboard, but it can also
  // REJECT — the execCommand fallback throws on a detached document. Without this the rejection is
  // unhandled inside a keydown listener, and the operator gets no message at all for a failed copy,
  // which is the same outcome the false branch exists to prevent.
  copyText(text)
    .then((ok) => toast(ok ? 'Console copied' : 'Copy failed', ok ? 'ok' : 'error'))
    .catch(() => toast('Copy failed', 'error'));
}
