// UI primitives for the Dashboard Next SPA (DASHBOARD_REBUILD_PLAN §0.5): toast
// notifications, promise-based inline dialogs that replace the browser's blocking
// prompt()/confirm() (which steal focus, can't be styled, and break the dark theme),
// and an async-action wrapper that surfaces a failure as a toast instead of an
// unhandled promise rejection. All DOM is created lazily — no index.html changes needed.
import { esc } from './util.js';

let toastHost = null;
function ensureToastHost() {
  if (toastHost && toastHost.isConnected) return toastHost;
  toastHost = document.createElement('div');
  toastHost.className = 'toast-host';
  document.body.appendChild(toastHost);
  return toastHost;
}

// Show a transient toast. tone ∈ {info, ok, warn, error}. Click dismisses; auto-dismisses
// after `timeout` ms (0 = sticky). Returns a close() fn.
export function toast(message, tone = 'info', { timeout = 4200 } = {}) {
  const host = ensureToastHost();
  const el = document.createElement('div');
  el.className = `toast toast-${tone}`;
  // errors/warnings are assertive (announced immediately); info/ok are polite.
  el.setAttribute('role', (tone === 'error' || tone === 'warn') ? 'alert' : 'status');
  el.textContent = String(message ?? '');
  // Cap the stack so a burst (WS reconnect loop, repeated save failures) can't push toasts
  // off the top of the viewport — evict the oldest beyond 4.
  while (host.children.length >= 4) host.firstElementChild?.remove();
  host.appendChild(el);
  requestAnimationFrame(() => el.classList.add('show'));
  let timer = null;
  const close = () => {
    if (timer) { clearTimeout(timer); timer = null; }
    el.classList.remove('show');
    setTimeout(() => el.remove(), 220);
  };
  el.addEventListener('click', close);
  if (timeout > 0) timer = setTimeout(close, timeout);
  return close;
}

// Promise-based modal. Resolves to: confirm → boolean; prompt → string|null (null = cancel).
function openDialog({ title = '', message = '', kind = 'confirm', defaultValue = '', confirmLabel = 'Confirm', cancelLabel = 'Cancel', tone = '' }) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'dialog-overlay';
    const isPrompt = kind === 'prompt';
    overlay.innerHTML = `
      <div class="dialog${tone ? ' dialog-' + esc(tone) : ''}" role="dialog" aria-modal="true">
        ${title ? `<h3 class="dialog-title">${esc(title)}</h3>` : ''}
        <p class="dialog-message">${esc(message)}</p>
        ${isPrompt ? '<input class="dialog-input" type="text" autocomplete="off">' : ''}
        <div class="dialog-actions">
          <button class="ghost dialog-cancel" type="button">${esc(cancelLabel)}</button>
          <button class="primary dialog-confirm${tone === 'danger' ? ' danger' : ''}" type="button">${esc(confirmLabel)}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    const input = overlay.querySelector('.dialog-input');
    // a11y: aria-modal asserts a focus trap, so actually implement one — remember the trigger,
    // keep Tab cycling inside the dialog, and restore focus on close.
    const previouslyFocused = document.activeElement;
    const focusables = () => Array.from(overlay.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')).filter((el) => !el.disabled && el.offsetParent !== null);
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      overlay.remove();
      document.removeEventListener('keydown', onKey, true);
      try { if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus(); } catch {}
      resolve(value);
    };
    const onConfirm = () => done(isPrompt ? (input ? input.value : '') : true);
    const onCancel = () => done(isPrompt ? null : false);
    overlay.querySelector('.dialog-confirm').addEventListener('click', onConfirm);
    overlay.querySelector('.dialog-cancel').addEventListener('click', onCancel);
    overlay.addEventListener('click', (event) => { if (event.target === overlay) onCancel(); });
    const onKey = (event) => {
      // stopPropagation so Escape/Enter don't ALSO reach document-level handlers
      // (Escape canceling a confirm was dismissing the inspector beneath it — review #14).
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onCancel(); }
      else if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.stopPropagation(); onConfirm(); }
      else if (event.key === 'Tab') {
        const items = focusables();
        if (!items.length) return;
        const first = items[0], last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener('keydown', onKey, true);
    if (input) { input.value = defaultValue; setTimeout(() => input.focus(), 30); }
    else setTimeout(() => overlay.querySelector('.dialog-confirm')?.focus(), 30);
  });
}

// Drop-in replacements for confirm()/prompt() (async, themed, non-blocking).
export function uiConfirm(message, opts = {}) {
  return openDialog({ ...opts, message, kind: 'confirm', confirmLabel: opts.confirmLabel || 'Confirm', cancelLabel: opts.cancelLabel || 'Cancel' });
}
export function uiPrompt(message, opts = {}) {
  return openDialog({ ...opts, message, kind: 'prompt', defaultValue: opts.defaultValue || '', confirmLabel: opts.confirmLabel || 'OK', cancelLabel: opts.cancelLabel || 'Cancel' });
}

// Wrap an async handler so a rejection becomes a toast, not an unhandled rejection
// (today several delegated handlers reject silently). Returns a function that never throws.
export function asyncAction(fn, label = 'Action') {
  return (...args) => Promise.resolve().then(() => fn(...args)).catch((err) => {
    toast(`${label} failed: ${err && err.message ? err.message : err}`, 'error');
    console.error(`[dashboard] ${label} failed`, err);
  });
}

// Global safety net: any otherwise-unhandled promise rejection toasts instead of dying
// silently in the console (call once at init).
export function installRejectionToast() {
  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason;
    toast(`Unexpected error: ${reason && reason.message ? reason.message : reason}`, 'error');
  });
}

// Lifted out of app.js in v0.5.4, where it was one of three shared names -- with `state` and
// `apiBase` -- that every subject slice in that file needed and none could import back, app.js being
// where they were declared. It lands here rather than in util.js because this is the DOM-facing
// module and util.js is the pure-value one.
//
// The braceless body is deliberate and byte-identical to app.js's: the reconstruction proof compares
// moved declarations exactly. It also briefly failed the harness's module-scope-browser-globals check,
// which read `document` here as load-time code -- it is not, it runs only when called, and the check
// was corrected rather than this line reworded to suit it.
export const byId = (id) => document.getElementById(id);
