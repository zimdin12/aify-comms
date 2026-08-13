// How MCP tool responses are WORDED: inbox lines, dispatch state, queued runs, auto-reply text.
//
// v0.5.4 layer 1 of the server.js decomposition, bounded deliberately. The reviewer's condition was a
// narrow subject with narrow exports, explicitly NOT a `server-utils` barrel that would just give the
// bridge a second monolith address. These nine are the subset of the 55 state-free helpers that are
// PURE and SELF-CONTAINED: no I/O, no module state, and no calls to any other server.js function.
// Everything else in that 55 either reaches a sibling helper or touches the environment, and belongs to
// a later, separately-measured seam.
//
// WHY THESE ARE WORTH MOVING beyond the line count: they decide what an operator READS. A wrong word in
// `formatInboxMessage` or a dropped field in `formatQueuedRun` is not a crash, it is a person being
// misinformed about their fleet — and until now none of it was reachable from a test, because server.js
// is the bin entry point and nothing imports it.
//
// PURE BY CONSTRUCTION, which is the property the tests below rely on: every function here takes plain
// values and returns a string. If a future edit needs a database read or a module global, it does not
// belong in this file.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND wrappers relaunch.

export function formatDispatchState(info = {}) {
  const state = info.dispatchState || {};
  const active = state.activeRun;
  const lines = [];
  if (active?.runId) {
    lines.push(`  Active run: ${active.runId} [${active.status || "running"}]`);
    if (active.subject) lines.push(`    Subject: ${active.subject}`);
  }
  if (Number(state.queuedRuns || 0) > 0) {
    lines.push(`  Queued runs: ${state.queuedRuns}`);
  }
  return lines.join("\n");
}

export function formatQueuedRun(run = {}) {
  let text = `${run.targetAgentId} (${run.runId})`;
  if (run.steered || run.status === "steered") {
    const target = run.steeredIntoActiveRun || {};
    text += ` steered into active run ${target.runId || run.runId}`;
    if (target.subject) {
      text += ` (${target.subject})`;
    }
    return text;
  }
  if (run.merged && Number(run.mergedCount || 0) > 1) {
    text += ` buffered ${run.mergedCount} updates`;
  }
  if (run.queuedBehindActiveRun?.runId) {
    text += ` queued behind active run ${run.queuedBehindActiveRun.runId}`;
    if (run.queuedBehindActiveRun.subject) {
      text += ` (${run.queuedBehindActiveRun.subject})`;
    }
  }
  return text;
}

export function formatOutboundActivity(info = {}) {
  const answered = !!info && typeof info === "object" && Object.hasOwn(info, "outbound");
  const o = (info && info.outbound) || {};
  const sent = String(o.lastSentAt || "").trim();
  const ran = String(o.lastCompletedRunAt || "").trim();
  if (!sent && !ran) {
    return answered
      ? "Last produced: none recorded (the service answered — no message sent, no run completed)"
      : "Last produced: unknown (service did not report outbound activity — pre-v0.3.1 service)";
  }
  const bits = [];
  if (sent) bits.push(`sent ${sent}`);
  if (ran) bits.push(`completed a run ${ran}`);
  return `Last produced (OUTBOUND): ${bits.join("; ")}`;
}

export function formatInboxHeaders(m, registry) {
  const senderInfo = registry?.agents?.[m.from];
  const rolePart = senderInfo ? ` (${senderInfo.role})` : "";
  const readTag = m._read || m.read ? " [read]" : " [NEW]";
  const preview = String(m.preview || m.body || "").trim();
  return (
    `--- ${m.id}${readTag} ---\n` +
    `From: ${m.from}${rolePart}\n` +
    `Type: ${m.type} | Subject: ${m.subject}\n` +
    `Time: ${m.timestamp ? new Date(m.timestamp).toISOString() : "?"}` +
    (m.inReplyTo ? `\nReply to: ${m.inReplyTo}` : "") +
    (preview ? `\nPreview: ${preview}` : "")
  );
}

export function formatInboxMessage(m, registry) {
  const senderInfo = registry?.agents?.[m.from];
  const rolePart = senderInfo ? ` (${senderInfo.role})` : "";
  const readTag = m._read || m.read ? " [read]" : " [NEW]";
  const safeBody = "```\n" + (m.body || "").replace(/```/g, "'''") + "\n```";
  return (
    `--- ${m.id}${readTag} ---\n` +
    `From: ${m.from}${rolePart}\n` +
    `Type: ${m.type} | Subject: ${m.subject}\n` +
    `Time: ${m.timestamp ? new Date(m.timestamp).toISOString() : "?"}\n` +
    (m.inReplyTo ? `Reply to: ${m.inReplyTo}\n` : "") +
    `\n${safeBody}`
  );
}

export function autoReplySubjectForRun(run = {}, terminalStatus = "completed") {
  const subject = String(run.subject || run.id || "dispatch result").trim();
  if (terminalStatus === "failed") return `[FAILED] ${subject}`;
  if (terminalStatus === "cancelled") return `[CANCELLED] ${subject}`;
  return `Re: ${subject}`;
}

export function autoReplyBodyForRun(run = {}, terminalStatus = "completed", detailText = "") {
  const detail = String(detailText || "").trim() ||
    (terminalStatus === "failed" ? "Run failed." : terminalStatus === "cancelled" ? "Run cancelled." : "Run completed.");
  if (terminalStatus === "completed") return detail;
  const intro =
    terminalStatus === "failed"
      ? "The run failed before the agent sent a chat reply."
      : "The run was cancelled before the agent sent a chat reply.";
  return `${intro}\n\n${detail}`;
}

export function replyExpectationSummary(run = {}) {
  if (!run.requireReply) return "reply not required";
  if (run.resultMessageId) return `reply sent (${run.resultMessageId})`;
  if (run.replyPending) return "reply pending";
  return "reply expected";
}
