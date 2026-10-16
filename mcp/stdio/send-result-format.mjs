// What `comms_send` tells the agent after a triggered send, from the service's answer alone.
//
// ONE RULE: say only what the answer proves. A dispatch run is a queued request, not a started worker; a
// console delivery is queued input; a recipient in `notStarted` was stored without a wake. An answer with
// none of them proves the message was stored and nothing more. The old text said "Dispatch: started" for
// an empty run list, which claimed the one thing the answer could not show.
//
// IT READS BOTH SERVICE VERSIONS. An older service refuses a woken send to an agent that cannot start
// (ok:false, nothing stored). From 0.9.8 the service stores it, lists the agent in `notStarted` and adds
// a `notice` (Steven, 2026-10-16). A nonce replay from an older service carries no `wakeOutcome`; either
// way a replay cannot say whether the first send woke anyone, so it reads as unknown.
//
// Pure: the service's answer and the call's arguments in, the tool result out.

import { awaitingReplyNote, formatQueuedRun } from "./tool-response-format.mjs";

const ACK_NOTE =
  " This ack reports what was CREATED, not what was delivered -- confirm with comms_run_status(...) before reporting delivery to anyone.";

export function formatSendRefusal(r = {}) {
  const skipped = (r.notStarted || []).map((x) => `${x.targetAgentId}: ${x.reason}${x.recipientStatus ? ` (${x.recipientStatus})` : ""}`);
  return {
    content: [{ type: "text", text: `${r.error || "Message was not sent."}${skipped.length ? `\nUnavailable: ${skipped.join("; ")}` : ""}` }],
    isError: true,
  };
}

export function formatTriggeredSendResult(r = {}, { from, to, type, requireReply } = {}) {
  if (r.replayed) {
    return {
      content: [{
        type: "text",
        text: `Already sent: this matched an earlier send of the same message (${r.messageId}), which the service stored. ` +
          "Whether that send woke its recipients is not known from this answer, and nothing was woken again.",
      }],
    };
  }
  const queued = (r.dispatchRuns || []).map((x) => formatQueuedRun(x));
  const consoles = (r.consoleDeliveries || []).map((x) => x.agentId || x.targetAgentId || x.terminalId).filter(Boolean);
  const unwoken = (r.notStarted || []).map((x) => `${x.targetAgentId}: ${x.reason}`);
  const lines = [];
  if (queued.length) lines.push(`Sent. Dispatch: ${queued.join(", ")}.${ACK_NOTE}`);
  if (consoles.length) lines.push(`Sent. Typed into the console of ${consoles.join(", ")} (queued input, not proof the agent read it).`);
  if (unwoken.length) {
    lines.push(`Saved in the inbox, not woken: ${unwoken.join("; ")}.`);
    if (r.notice) lines.push(r.notice);
  }
  if (!lines.length) lines.push(`Stored (${r.messageId}). No dispatch was created, so nothing was started.`);
  // A stored-unwoken request still owes its reply, so the don't-predict-it note applies to every result.
  return { content: [{ type: "text", text: lines.join("\n") + awaitingReplyNote({ from, to, type, requireReply }) }] };
}
