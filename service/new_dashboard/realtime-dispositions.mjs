// What the dashboard does with each event the service broadcasts.
//
// THE DEFECT THIS REPLACES. applyRealtimeEvent ended with an inline array of eleven event names that
// triggered a refresh. Anything else fell off the end of the function and was dropped — no branch, no
// log, nothing. Measured 2026-08-26: the service broadcasts 51 distinct event names -- 49 to every
// connection via broadcast(), and 2 (new_message, dispatch_request) addressed to a single agent
// socket via notify_agent(). The gate's producer scan read only the first sender until 2026-08-26,
// which is why the figure here was 49. Of those 49, three were handled
// in place, eleven were in the array, and THIRTY-FIVE were silently discarded, among them
// channel_message, terminal_stopped, message_deleted, conversation_cleared, file_shared and all three
// spawn_request_*. Not data loss — the ~15s poll catches up — but a realtime channel that mostly is not.
//
// The list is the defect, not its contents. It is a hand-maintained allowlist against a producer set in
// another language in another directory, and nothing compared the two. A new broadcast added
// service-side joins the dropped set silently and stays there. "Derive allowed values, never list them"
// is the repo's own rule; this is the closest thing to deriving it, since the disposition genuinely is
// a judgement per event and cannot be computed — so instead every event must CARRY one, and
// realtime-dispositions.test.mjs fails if the service emits a name with no entry here.
//
// THREE DISPOSITIONS:
//   granular — handled in place by applyRealtimeEvent, which patches state and re-renders without a
//              refetch. The cheapest, and the reason the list is not simply "refresh everything".
//   refresh  — call refreshSoon(). It debounces 250ms and coalesces while a bundle is in flight, so a
//              burst collapses into one refetch. This is why moving an event here is cheap.
//   ignore   — deliberately dropped, and the REASON is required. An entry with no reason fails the
//              test, because "we ignore this" with no argument is how the original array grew.

/** @typedef {'granular'|'refresh'|'ignore'} Disposition */

/**
 * Handled in place. Listed here so the gate sees them, but applyRealtimeEvent owns the behaviour —
 * duplicating the patching logic in a table would be two places doing one job.
 */
export const GRANULAR = Object.freeze([
  'agent_status',
  'terminal_output',
  'terminal_started',
]);

/**
 * Deliberately dropped, each with the reason it is dropped. Anything not here and not granular gets a
 * refresh, so the DEFAULT is to react rather than to discard — the opposite of the code this replaces.
 */
export const IGNORED = Object.freeze({
  environment_heartbeat:
    'The highest-frequency event measured on this deployment (2 in 60s while the fleet was idle, and it '
    + 'scales with bridge count). It carries liveness only, and the environments panel already refreshes '
    + 'on the ordinary poll, so reacting to each one buys a fresher lastSeen for a steady refetch cost.',
  terminal_control_requested:
    'The request half of a control. The dashboard issued it, so it already knows; what it needs to see '
    + 'is the outcome, and terminal_control_updated carries that.',
  agent_control_requested:
    'Same shape: the request the dashboard just made. agent_worker_stopped and agent_status report what '
    + 'actually happened.',
  environment_control_requested:
    'The request half of an environment control, which the dashboard itself issued and therefore '
    + 'already knows about. What it needs to see is whether the environment accepted it, and '
    + 'environment_control_updated carries that outcome.',
});

// dispatch_control_requested is NOT here on purpose: it was on the original eleven-name refresh list
// and still refreshes. Moving it would be a behaviour change dressed up as a refactor.

/**
 * The disposition of one event name.
 *
 * UNKNOWN NAMES REFRESH. A name this file has never heard of is more likely a new broadcast than a
 * mistake, and a stale panel is a worse failure than one extra debounced refetch. The test is what
 * stops that default from becoming the silent drop it replaced.
 *
 * @param {string} event
 * @returns {Disposition}
 */
export function dispositionOf(event) {
  const name = String(event || '');
  if (GRANULAR.includes(name)) return 'granular';
  if (Object.prototype.hasOwnProperty.call(IGNORED, name)) return 'ignore';
  return 'refresh';
}

/** Why an event is ignored, or null when it is not. For a caller that wants to explain itself. */
export function ignoredReason(event) {
  const name = String(event || '');
  return Object.prototype.hasOwnProperty.call(IGNORED, name) ? IGNORED[name] : null;
}
