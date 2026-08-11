// Should the OPEN inspector drawer be re-rendered when fresh data arrives?
//
// THE BUG, operator-reported 2026-08-11: "when i have inspector open and status changes, it does not
// update." Correct, and it is not a rendering glitch — there was no refresh path at all. Every
// `openXInspector()` renders HTML into the drawer once and records `state.inspector = {kind, ...}`;
// nothing in the poll loop or the WebSocket handler ever re-rendered it. The list rows behind the
// drawer updated, the drawer stayed a snapshot from the moment it was opened.
//
// WHY THE OBVIOUS FIX IS WRONG, and probably why this was never done: several inspectors contain
// EDITABLE fields — the environment-roots textarea, the agent-edit form, the spawn-continuation
// form. Re-rendering those on a 2-second poll would destroy whatever the operator was typing, along
// with focus and scroll position. Turning "stale drawer" into "drawer that eats your input" is a
// worse bug, not a fix.
//
// So the rule is per-kind and FAILS CLOSED: only kinds explicitly known to be read-only refresh. An
// unrecognised kind is never auto-refreshed, because the cost of being wrong is asymmetric — a stale
// read-only drawer is an annoyance, a wiped form is lost work.
//
// Pure module with its own tests, deliberately: `app.js` is ~5k lines and only reachable by
// source-regex tests, which cannot fail on wrong logic. Every decision here is a branch worth
// failing a test on.

// Read-only views. Re-rendering these is safe: nothing in them holds unsaved operator input.
export const REFRESHABLE_INSPECTOR_KINDS = new Set([
  'agent',                // agent detail — the one the operator actually reported
  'run',                  // dispatch run + its events
  'history',              // message/run history
  'identity-directory',   // registered identities
  'message',              // a single message
]);

// Kinds that hold operator input. Named explicitly rather than inferred, so adding a new form
// inspector is a deliberate decision here instead of an accidental data-loss bug.
export const FORM_INSPECTOR_KINDS = new Set([
  'env-roots',    // roots textarea
  'agent-edit',   // agent settings form
  'continue',     // spawn-continuation form
]);

/**
 * Decide whether an open drawer may be re-rendered right now.
 *
 * Returns a REASON string rather than a boolean: when a drawer does not refresh, "why" is the only
 * useful question, and a boolean cannot answer it. Callers treat `'refresh'` as the go signal.
 */
export function inspectorRefreshDecision(inspector, {
  isOpen = false,
  isEditingFocus = false,
  isLoading = false,
} = {}) {
  if (!isOpen) return 'closed';
  const kind = String((inspector || {}).kind || '').trim();
  if (!kind) return 'no-kind';
  if (FORM_INSPECTOR_KINDS.has(kind)) return 'form';
  if (!REFRESHABLE_INSPECTOR_KINDS.has(kind)) {
    // Fail closed. A kind nobody has classified might hold input we cannot see.
    return 'unknown-kind';
  }
  // ONLY an EDITING focus suppresses the refresh — not focus anywhere inside the drawer.
  //
  // BROWSER-VERIFIED FAILURE of my first version, 2026-08-11: clicking a row that opens a drawer
  // leaves `document.activeElement` on a BUTTON inside it, so `containsFocus` was true immediately
  // and forever, and the drawer NEVER refreshed. Zero DOM mutations in 11 seconds — the fix was
  // suppressed by its own guard in the exact case it was written for (open a drawer, watch it).
  //
  // A focused BUTTON holds nothing that can be lost. A focused input/textarea/select or a
  // contenteditable does. Guard the thing that actually has state, not proximity to it.
  if (isEditingFocus) return 'editing';
  // Its own fetch is still in flight; re-entering would race it.
  if (isLoading) return 'loading';
  return 'refresh';
}

export function shouldRefreshInspector(inspector, context) {
  return inspectorRefreshDecision(inspector, context) === 'refresh';
}
