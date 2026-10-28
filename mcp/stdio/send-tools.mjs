// The two tools that SEND a message, and the delivery cluster they share.
//
// Extracted from server.js in v0.5.4 — the last tool registrations to leave it. One module because the two
// share the send path and how its answer is rendered.
//
// Both decide WAKE. A direct send to an agent that cannot start is stored unwoken from 0.9.8 (an older
// service refused it); a channel post is always stored and only the members that can start are woken. That is the property worth remembering when
// reading them — the tool's job is to decide deliverability and then either steer, queue, or cold-start,
// not merely to append to an inbox.

import { randomUUID } from "crypto";

import { httpCall } from "./aify-service-endpoint.mjs";
import { validateName } from "./safe-name.mjs";
import { formatSendRefusal, formatTriggeredSendResult } from "./send-result-format.mjs";
import { formatQueuedRun } from "./tool-response-format.mjs";
import { quoteUntrustedSubject } from "./quote-subject.mjs";

// EVERY AGENT PAYS THIS ON EVERY TURN. `tools/list` is always-loaded context, so a sentence here is
// not written once -- it is re-read by every agent on every turn for the life of the fleet. It was
// 2,638 characters; the rule applied to each sentence was whether it changes what the CALLER does.
//
// What went, and why, so it does not creep back:
//   - Resident-vs-environment-managed trigger mechanics. True, and the caller cannot act on it:
//     they do not choose the delivery path. Internals, not a contract.
//   - The `managed_reply_capture_fallback` safety net, 294 characters, the longest sentence in the
//     whole description. It named a config flag the agent cannot read and then told it not to rely
//     on the behaviour -- so it changed nothing above the positive instruction two sentences up.
//   - Deliverability was stated THREE times (managed-at-available, "`available` and `blocked` are
//     both deliverable", blocked/completed-are-status-notes). One meaning, three places.
//   - The two busy branches and the two queueIfBusy clauses, each a single fact split in half.
//
// And "do not send a courtesy acknowledgement" is now "leave it unanswered": a prohibition drags the
// banned behaviour into context and makes it more available, so the target behaviour is named
// instead. The requireReply phrasing is load-bearing -- three tests match on "omit requireReply",
// "set requireReply=true" and "set requireReply=false", because each mode has to be findable.
export const COMMS_SEND_TOOL_DESCRIPTION =
  "Send a message to an agent by ID, or to every agent with a role. `dashboard` stores it for the operator without waking anyone: send there what they asked for, and problems that need a human. " +
  "A new message to an offline, stopped or misconfigured target, or one with no live wake path, is saved unwoken (older services refuse it); the answer says which. A reply is always stored. An `available` managed agent is cold-started by the send, and a `blocked` one gets it too. A busy target is steered into its active run when it can steer, and gets it next turn when it cannot. " +
  "REPLY WITH A TOOL CALL: comms_send(type=\"response\", inReplyTo=<the message id>). That call is the reply and closes the run; your final plain text is never delivered. Requests, reviews, errors and dashboard asks owe a reply. Leave a response, approval, info or acknowledgement with no new question or work unanswered. Answer input typed into your terminal there. " +
  "Omit requireReply to keep the type default: request, review and error owe a reply, the rest do not. Set requireReply=true to track a reply on the rest. If you set requireReply=false on a request, review or error, the recipient is told no reply is tracked while the Work Loop still tracks one. requireReply never affects waking.";

export function registerSendTools(server, z) {

  server.tool(
    "comms_send",
    COMMS_SEND_TOOL_DESCRIPTION,
    {
      from: z.string().describe("Your agent ID"),
      to: z.string().optional().describe("Target agent ID"),
      toRole: z.string().optional().describe("Every agent with this role; wakes only running ones"),
      type: z
        .enum(["request", "response", "info", "error", "review", "approval"])
        .describe("Message type"),
      subject: z.string().describe("Short subject"),
      body: z.string().describe("Message content"),
      priority: z.enum(["normal", "high", "urgent"]).optional().describe("Message priority (default: normal)"),
      inReplyTo: z.string().optional().describe("Message ID this replies to"),
      steer: z.boolean().optional().describe("Default true: a busy target that can steer gets it between tool calls; one that cannot gets it next turn."),
      queueIfBusy: z.boolean().optional().describe("true: deliver next turn, behind its current work, even if it could steer."),
      requireReply: z.boolean().optional().describe("Leave unset; see the description."),
    },
    async ({ from, to, toRole, type, subject, body, priority, inReplyTo, steer, queueIfBusy, requireReply }) => {
      if (!to && !toRole) {
        return { content: [{ type: "text", text: "Error: need 'to' or 'toRole'" }], isError: true };
      }
      const shouldTrigger = true;
      const forceQueue = queueIfBusy === true;

      // -- Remote mode --
      // Stable idempotency key (#240): minted once per logical send so httpCall can retry
      // the POST safely on a transient socket error (the server collapses the retry to the
      // original message) instead of dropping it. One nonce per tool call — a real second
      // send is a new tool call with a fresh nonce.
      const clientNonce = randomUUID();
      const r = await httpCall("POST", "/messages/send", {
        from_agent: from, to, toRole, type, subject, body, priority: priority || "normal", inReplyTo, trigger: shouldTrigger, steer: forceQueue ? false : (steer ?? true), queueIfBusy: forceQueue, requireReply, clientNonce,
      });
      if (!r.ok) return formatSendRefusal(r);

      const dashboardOnly = (r.recipients || []).length > 0 && (r.recipients || []).every((rid) => rid === "dashboard");
      if (shouldTrigger && (r.replayed || (r.recipients?.length > 0 && !dashboardOnly))) {
        return formatTriggeredSendResult(r, { from, to, type, requireReply });
      }

      // Include recipient status in response
      const statusParts = (r.recipients || []).map(rid => {
        const info = r.recipientStatus?.[rid];
        if (info) return `${rid} [${info.status}, ${info.unread} unread]`;
        return rid;
      });
      return {
        content: [{ type: "text", text: `Sent (${r.messageId}) to ${statusParts.join(", ")}. Subject: ${quoteUntrustedSubject(subject, 240)}` }],
      };
    }
  );

  server.tool(
    "comms_channel_send",
    "Post to a channel. The post and every member's inbox copy are always stored. Members that cannot be woken (offline, stopped, misconfigured, or no live wake path) are listed under Not started. Busy members are steered or queued as with comms_send.",
    {
      channel: z.string().describe("Channel name"),
      from: z.string().describe("Your agent ID"),
      body: z.string().describe("Message content"),
      type: z
        .enum(["info", "request", "response", "error", "review", "approval"])
        .optional()
        .describe("Message type (default: info)"),
      priority: z.enum(["normal", "high", "urgent"]).optional().describe("Message priority (default: normal)"),
      steer: z.boolean().optional().describe("Default true: busy members that can steer get it between tool calls; the rest get it next turn."),
      queueIfBusy: z.boolean().optional().describe("true: deliver next turn for everyone. Overrides steer."),
    },
    async ({ channel, from, body, type, priority, steer, queueIfBusy }) => {
      try { validateName(channel, "channel name"); } catch (e) { return { content: [{ type: "text", text: e.message }], isError: true }; }
      const shouldTrigger = true;
      const forceQueue = queueIfBusy === true;
      const subject = `#${channel}: ${body.slice(0, 80)}`;

      const r = await httpCall("POST", `/channels/${encodeURIComponent(channel)}/send`, {
        from_agent: from, channel, body, type: type || "info", priority: priority || "normal", trigger: shouldTrigger, steer: forceQueue ? false : (steer ?? true), queueIfBusy: forceQueue,
      });
      if (!r.ok) {
        const skipped = (r.notStarted || []).map((x) => `${x.targetAgentId}: ${x.reason}${x.recipientStatus ? ` (${x.recipientStatus})` : ""}`);
        return {
          content: [{
            type: "text",
            text: `${r.error || `Channel message to #${channel} was not sent.`}${skipped.length ? `\nUnavailable: ${skipped.join("; ")}` : ""}`,
          }],
          isError: true,
        };
      }
      if (shouldTrigger && (r.dispatchRuns?.length || r.notStarted?.length)) {
        const queued = (r.dispatchRuns || []).map((x) => formatQueuedRun(x));
        const skipped = (r.notStarted || []).map((x) => `${x.targetAgentId}: ${x.reason}`);
        // No run created is not a start: the post is stored and every member's copy waits in its inbox.
        const outcome = queued.length
          ? `Dispatch: ${queued.join(", ")}. This ack reports what was CREATED, not what was delivered -- confirm with comms_run_status(...) before reporting delivery.`
          : "No member was woken: the post and every member's inbox copy are stored.";
        return {
          content: [{
            type: "text",
            text: `Sent to #${channel}. ${outcome}` + (skipped.length ? `\nNot started: ${skipped.join("; ")}` : ""),
          }],
        };
      }
      return { content: [{ type: "text", text: `Sent to #${channel} (${r.members.length} members).` }] };
    }
  );

}
