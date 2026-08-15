// WHAT a resident claude session is told, and WHETHER to re-pulse turn_busy.
//
// Extracted from `claude-channel.js` in v0.5.4. All three are pure — a payload in, a string or a
// verdict out — while everything they were sitting among opens sockets, polls the service and
// emits MCP notifications. That mix is why `controlContent` had no test: it was module-private in
// a file whose other half cannot run without a bridge.
//
// THESE ARE BEHAVIOUR, NOT FORMATTING. Each carries a bug that was found in production and is
// recorded in its own comment:
//   * `dispatchContent`'s same-turn reply line exists because a session that split read from reply
//     stranded the reply ~20 minutes — a managed session is not re-woken to finish one.
//   * `decideRepulse` gates on the run being IN-FLIGHT. Re-pulsing for a `delivered` run that only
//     owes a reply kept an idle agent lit as "working".
//
// Bodies byte-identical to what stood in `claude-channel.js`; `controlContent` gained `export`,
// which is the only substitution.

import { claudeAifyReceiptLine } from "./aify-console-markers.js";

export function dispatchContent(agentId, run) {
  const body = String(run.body || "").replace(/```/g, "'''");
  const priority = (run.priority || "normal").toLowerCase();
  const priorityLabel =
    priority === "urgent" ? "URGENT" :
    priority === "high" ? "HIGH" :
    "NORMAL";
  const actionLine =
    priority === "urgent" ? "Drop current work and handle this immediately." :
    priority === "high" ? "Read before continuing current work." :
    "Handle when you reach a natural break.";
  const requireReply = !!run.requireReply;
  // require_reply dispatches MUST be answered in THIS turn. A managed/channel
  // session goes idle after the turn ends and is NOT re-woken to finish a
  // deferred reply — root-caused 2026-06-02: a session that split read (turn 1)
  // from reply (turn 2) stranded the reply ~20min until the next dispatch
  // happened to re-wake it. So instruct the same-turn reply explicitly.
  // Terse on purpose (2026-06-18): the full "why same-turn" rationale lives once in the
  // MCP server `instructions` the session already loaded — don't re-pay it on every delivery.
  const replyLine = run.messageId
    ? (requireReply
        ? `Reply THIS turn before you end: comms_send(inReplyTo="${run.messageId}", ...). A deferred reply strands — the session is not re-woken for it.`
        : `When you reply, include inReplyTo="${run.messageId}".`)
    : "Reply through aify when the task is done.";
  return [
    claudeAifyReceiptLine(),
    `[${priorityLabel}] ${run.from || "unknown"} → ${agentId}: ${run.subject || "(no subject)"}`,
    actionLine,
    `From: ${run.from}`,
    `Subject: ${run.subject}`,
    priority !== "normal" ? `Priority: ${priority.toUpperCase()}` : "",
    run.messageId ? `Message ID: ${run.messageId}` : "",
    "",
    "Handle this directly in the current session.",
    replyLine,
    "",
    "```",
    body,
    "```",
  ].filter(Boolean).join("\n");
}


export function controlContent(agentId, control) {
  const body = String(control.body || "").replace(/```/g, "'''");
  const lines = [
    `Aify ${control.action} for agent "${agentId}".`,
    control.from ? `Requested by: ${control.from}` : "",
  ];
  if (body) {
    lines.push("", "```", body, "```");
  }
  if (control.action === "interrupt") {
    lines.push("", "Stop your current task as soon as practical. Send a brief status reply.");
  } else if (control.action === "steer") {
    lines.push("", "Apply this guidance to your current work.");
  }
  return lines.filter(Boolean).join("\n");
}


// Pure decision: given a /agents/{id} snapshot, should the channel
// bridge re-pulse turn_busy on this poll cycle? Exported for tests.
// Returns { repulse: boolean, runId: string }. See the call site +
// 2026-05-23 feedback-loop discussion in pollLoop for rationale.
export function decideRepulse(agentSnapshot = {}) {
  const dispatchState = agentSnapshot.dispatchState || {};
  const hasActiveRun = Boolean(dispatchState.hasActiveRun);
  if (!hasActiveRun) return { repulse: false, runId: "" };
  // Re-pulse turn_busy ONLY for an IN-FLIGHT run (claimed/running). A
  // require_reply run that's been delivered sits in 'delivered' while the
  // agent — which already finished the turn — merely owes a reply. Re-pulsing
  // turn_busy for that keeps the server's `elif turn_busy` branch lighting up
  // "working" instead of the intended idle "online / awaiting reply" state.
  // (operator-reported 2026-06-01: idle agent stuck at "working" while only
  // owing a reply.) The server's `activeRun.status` carries the run status
  // (api_v2 _format_dispatch_state); gate on it. Note: today the snapshot's
  // dispatch-state query only selects claimed/running, but gating here is the
  // correct contract regardless of which runs the serializer surfaces, and
  // preserves the anti-feedback-loop property (no re-pulse off derived status).
  const activeRun = dispatchState.activeRun || {};
  const status = String(activeRun.status || "").trim().toLowerCase();
  const inFlight = status === "claimed" || status === "running";
  if (!inFlight) return { repulse: false, runId: "" };
  const activeRunId = String(activeRun.runId || "");
  return { repulse: true, runId: activeRunId };
}
