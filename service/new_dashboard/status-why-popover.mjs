// The "why is it this status?" popover: what it says, where it sits, and returning focus when it closes.
//
// Small, and a real subject rather than a leftover. It owns three things worth failing a test on: the
// reason text falls back through `data-status-why` → `title` → a default, so a chip with no reason still
// explains itself; the position is clamped to the viewport, so a chip near the right or bottom edge does
// not open a popover off-screen; and the trigger that opened it is remembered so focus returns there on
// close. That last one is the accessibility contract — a dialog that drops focus to the document leaves a
// keyboard user at the top of a 4,000-line page.
//
// `_statusWhyReturnFocus` moves with it because nothing outside this closure reads it: the ownership test
// throughout this series is a count of DIRECT readers, and a focus latch with two owners would restore the
// wrong element.
//
// Extracted from app.js in v0.5.4. It needs only `byId` and `esc`, both from sibling leaf modules imported
// downward — possible at all because `state` and `byId` were given owners earlier in the series.
//
// The declarations are byte-identical to those that stood in app.js; the only substitution is the added
// `export `, which the reconstruction proof strips before comparing. Their leading comments stayed behind
// in app.js deliberately — `declarationSpan` returns the declaration alone, so a span that took its
// comments could not round-trip through the proof.


import { byId } from './ui.js';
import { esc } from './util.js';

export let _statusWhyReturnFocus = null;
export function openStatusWhy(trigger) {
  const popover = byId('status-why-popover');
  if (!popover || !trigger) return;
  _statusWhyReturnFocus = trigger;
  const reason = trigger.dataset.statusWhy || trigger.title || 'No status reason loaded.';
  const kind = trigger.dataset.statusKind || 'unknown';
  popover.hidden = false;
  popover.setAttribute('role', 'dialog');
  popover.innerHTML = `
    <div class="item-title">
      <strong>Status: ${esc(kind)}</strong>
      <button class="ghost" data-close-status-why>Close</button>
    </div>
    <p>${esc(reason)}</p>`;
  setTimeout(() => popover.querySelector('[data-close-status-why]')?.focus(), 20);
  const rect = trigger.getBoundingClientRect();
  const top = Math.min(window.innerHeight - 160, Math.max(12, rect.bottom + 8));
  const left = Math.min(window.innerWidth - 320, Math.max(12, rect.left));
  popover.style.top = `${top}px`;
  popover.style.left = `${left}px`;
}
export function closeStatusWhy() {
  const popover = byId('status-why-popover');
  if (!popover) return;
  popover.hidden = true;
  popover.innerHTML = '';
  try { if (_statusWhyReturnFocus && _statusWhyReturnFocus.focus) _statusWhyReturnFocus.focus(); } catch {}
  _statusWhyReturnFocus = null;
}
