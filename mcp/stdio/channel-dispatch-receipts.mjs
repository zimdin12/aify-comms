// Recording what happened to a dispatch the Claude channel delivered. Extracted from
// `claude-channel.js` (v0.6 Phase 1) with the bodies byte-identical.
//
// WHY THIS MOVED. The coverage census reports 11 named functions in `claude-channel.js` that no test
// has ever called, and these are three of them. They were unreachable for the same structural reason as
// the dispatch-loop callbacks: they live beside a `pollLoop` that never returns, in a module whose only
// entry point starts a sidecar. `markDispatchDelivered` decides whether a run reads `delivered` or
// `completed` — the difference between "the agent still owes a reply" and "this is finished" — and
// nothing checked it.
//
// THE TRANSPORT IS INJECTED, not imported, and that is deliberate. `claude-channel.js` defines its OWN
// `httpCall` (a fork of the one in `aify-service-endpoint.mjs`, gating on ACTIVE_SERVER_URL and
// iterating SERVER_URLS). Importing the shared one here would quietly give these three a DIFFERENT
// transport from the rest of the sidecar — a second behaviour nobody asked for. Taking it as a
// parameter keeps the caller's transport and makes the calls assertable without a server.
//
// The fork itself is left alone: unifying two `httpCall`s is a behaviour change on a delivery path and
// belongs in its own slice with its own reasoning, not smuggled into an extraction.

/**
 * Is this run routed through the channel (rather than executed by a resident session)?
 * Pure; the receipt text below is the only thing that reads it.
 */
export function isChannelRun(run) {
  return String(run?.executionMode || "").trim().toLowerCase() === "channel";
}

/**
 * Build the two receipt writers over a caller-supplied transport.
 *
 * @param {object} deps
 * @param {Function} deps.httpCall  `(method, endpoint, body) => Promise<any>`
 */
export function makeDispatchReceipts({ httpCall }) {
  return {
    markDispatchDelivered: async function markDispatchDelivered(run) {
      // Any dispatch with require_reply=true stays in 'delivered' status until
      // the agent's explicit reply (via comms_send with inReplyTo) closes it.
      // This applies symmetrically to channel-route and resident-execution_mode
      // dispatches — both pass through this delivery path. Without it, resident
      // require_reply runs auto-completed on delivery and the dashboard had no
      // signal that the agent still owed a reply. Server-side derivation
      // (_current_channel_awaiting_reply_run_row) lights up "working" while
      // any such run is 'delivered'.
      const channelRun = isChannelRun(run);
      const requireReply = !!run?.requireReply;
      const runId = String(run?.id || "");
      const awaitingReply = requireReply;
      // D2 (#162): a routine require_reply delivery is normal-path, not noteworthy —
      // emit NO summary so the Runs audit view stays clean. The audit signal is the
      // 'delivered' event we append below; meaningful summaries are reserved for
      // failures/requeues (see markDispatchDeliveryFailed).
      await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(runId)}`, {
        status: awaitingReply ? "delivered" : "completed",
        summary: "",
        runtime: "claude-code",    appendEvent: channelRun
          ? "Delivered to Claude channel bridge"
          : "Delivered and completed by channel bridge",
        eventType: "delivered",
      });
    },
    markDispatchDeliveryFailed: async function markDispatchDeliveryFailed(runId, error) {
      await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(runId)}`, {
        status: "failed",
        error: error?.message || String(error),
        runtime: "claude-code",    appendEvent: `Claude channel delivery failed: ${error?.message || String(error)}`,
        eventType: "failed",
      });
    },
  };
}
