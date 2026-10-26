// WHAT a resident claude session is told.
//
// Extracted from `claude-channel.js` in v0.5.4. Both are pure — a payload in, a string or a
// verdict out — while everything they were sitting among opens sockets, polls the service and
// emits MCP notifications. That mix is why `controlContent` had no test: it was module-private in
// a file whose other half cannot run without a bridge.
//
// THESE ARE BEHAVIOUR, NOT FORMATTING. Each carries a bug that was found in production and is
// recorded in its own comment:
//   * `dispatchContent`'s same-turn reply line exists because a session that split read from reply
//     stranded the reply ~20 minutes — a managed session is not re-woken to finish one.
//
// Bodies byte-identical to what stood in `claude-channel.js`; `controlContent` gained `export`,
// which is the only substitution.

import { claudeAifyReceiptLine } from "./aify-console-markers.js";
import { describeSender } from "./tool-response-format.mjs";
import { quoteUntrustedSubject } from "./quote-subject.mjs";

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
    `[${priorityLabel}] ${run.from || "unknown"} → ${agentId}: ${run.subject ? quoteUntrustedSubject(run.subject, 240) : "(no subject)"}`,
    actionLine,
    `From: ${describeSender(run)}`,
    `Subject: ${run.subject ? quoteUntrustedSubject(run.subject, 240) : "(no subject)"}`,
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
