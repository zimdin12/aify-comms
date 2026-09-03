// The two tools that SEND a message, and the delivery cluster they share.
//
// Extracted from server.js in v0.5.4 — the last tool registrations to leave it. They were parked together
// behind `spawnTriggeredAgent`: both cold-start a managed target that is resting at `available`, and until
// that function had an owner neither could move.
//
// WHY THEY ARE ONE MODULE, correcting what channel-tools.mjs predicted. That module's header says
// `comms_channel_send` is "the fifth channel tool and belongs to this subject eventually". Measured before
// moving: the two senders share TEN of their twelve imported names — the delivery cluster — while
// channel-tools has six imports and four read-only tools. Putting the sender there would have doubled that
// module's import surface and split the delivery cluster across two files. Subject beat category.
//
// Both are LIVE-DELIVERY GATED: a send to an offline target is not written at all. That is the property
// worth remembering when reading them — the tool's job is to decide deliverability and then either steer,
// queue, or cold-start, not merely to append to an inbox.

import { randomUUID } from "crypto";
import fs from "fs";
import path from "path";

import { IS_REMOTE, httpCall } from "./aify-service-endpoint.mjs";
import { dedupePreserveOrder } from "./dedupe.mjs";
import { MESSAGES_DIR, deliverMessage, readAgents, writeAgents } from "./local-store.mjs";
import { canLaunchRuntime, normalizeRuntime } from "./runtimes.js";
import { validateName } from "./safe-name.mjs";
import { normalizeSessionMode } from "./session-mode.mjs";
import { spawnTriggeredAgent } from "./spawn-triggered-agent.mjs";
import { formatQueuedRun } from "./tool-response-format.mjs";

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
  "Send a message to an agent by ID, or to all agents with a given role. The target `dashboard` stores it for the operator without starting a runtime. " +
  "LIVE-DELIVERY GATED: a target that is offline, stopped, or has no live wake path is not written at all. `available` and `blocked` ARE deliverable, including a managed agent with no live worker yet (a hermes whose gateway died), which the send cold-starts; agent-reported blocked/completed are status notes, not delivery blockers. A busy target is steered into its active run between tool calls when it can steer, and queued as next-turn work when it cannot. queueIfBusy=true forces the queue and ignores steer. " +
  "REPLY WITH A TOOL CALL, in both live CLI sessions and dashboard-managed runs: comms_send(type=\"response\", inReplyTo=<the message id>). That call is the team-visible reply and closes the run; your final plain text is your own working output, not a delivered reply. Requests, reviews, errors, dashboard asks and explicit reply contracts owe one. A response, approval, info or acknowledgement carrying no new question or work is read context — leave it unanswered. Terminal input you typed yourself is answered in the terminal. " +
  "Omit requireReply for type-based behaviour (`request`, `review` and `error` owe replies; `info`, `response` and `approval` do not). Set requireReply=true to track a normally optional message; set requireReply=false to drop the contract on `info`, `response` and `approval` — it does not stop the Work Loop, which enrols `request`, `review` and `error` by type whatever the flag says. requireReply changes the reply contract, not whether the target is woken. " +
  "Keep each message on one topic and scoped to the recipient's own work, say what you checked when truth matters, and ask one clear question when blocked.";

export function registerSendTools(server, z) {

  server.tool(
    "comms_send",
    COMMS_SEND_TOOL_DESCRIPTION,
    {
      from: z.string().describe("Your agent ID"),
      to: z.string().optional().describe("Target agent ID"),
      toRole: z.string().optional().describe("Send to all agents with this role"),
      type: z
        .enum(["request", "response", "info", "error", "review", "approval"])
        .describe("Message type"),
      subject: z.string().describe("Short subject"),
      body: z.string().describe("Message content"),
      priority: z.enum(["normal", "high", "urgent"]).optional().describe("Message priority (default: normal)"),
      inReplyTo: z.string().optional().describe("Message ID this replies to"),
      steer: z.boolean().optional().describe("When true and target is busy, deliver between tool calls when supported; otherwise queue/merge as next-turn work. Defaults to true. Ignored when queueIfBusy=true."),
      queueIfBusy: z.boolean().optional().describe("When true, force next-turn queue/merge behind the target's active/queued work instead of steering the active turn."),
      requireReply: z.boolean().optional().describe("Reply-contract override. Omit for type defaults (request/review/error=true; info/response/approval=false). Set true only to track a response to a normally optional message; false drops the contract on info/response/approval and does NOT exempt request/review/error, which the Work Loop enrols by type."),
    },
    async ({ from, to, toRole, type, subject, body, priority, inReplyTo, steer, queueIfBusy, requireReply }) => {
      if (!to && !toRole) {
        return { content: [{ type: "text", text: "Error: need 'to' or 'toRole'" }], isError: true };
      }
      const shouldTrigger = true;
      const forceQueue = queueIfBusy === true;

      // -- Remote mode --
      if (IS_REMOTE) {
        // Stable idempotency key (#240): minted once per logical send so httpCall can retry
        // the POST safely on a transient socket error (the server collapses the retry to the
        // original message) instead of dropping it. One nonce per tool call — a real second
        // send is a new tool call with a fresh nonce.
        const clientNonce = randomUUID();
        const r = await httpCall("POST", "/messages/send", {
          from_agent: from, to, toRole, type, subject, body, priority: priority || "normal", inReplyTo, trigger: shouldTrigger, steer: forceQueue ? false : (steer ?? true), queueIfBusy: forceQueue, requireReply, clientNonce,
        });
        if (!r.ok) {
          const skipped = (r.notStarted || []).map((x) => `${x.targetAgentId}: ${x.reason}${x.recipientStatus ? ` (${x.recipientStatus})` : ""}`);
          return {
            content: [{
              type: "text",
              text: `${r.error || "Message was not sent."}${skipped.length ? `\nUnavailable: ${skipped.join("; ")}` : ""}`,
            }],
            isError: true,
          };
        }

        const dashboardOnly = (r.recipients || []).length > 0 && (r.recipients || []).every((rid) => rid === "dashboard");
        if (shouldTrigger && r.recipients?.length > 0 && !dashboardOnly) {
          const queued = (r.dispatchRuns || []).map((x) => formatQueuedRun(x));
          const skipped = (r.notStarted || []).map((x) => `${x.targetAgentId}: ${x.reason}`);
          return {
            content: [{
              type: "text",
              text:
                `Sent. Dispatch: ${queued.join(", ") || "started"}. This ack reports what was CREATED, not what was delivered -- confirm with comms_run_status(...) before reporting delivery to anyone. Requests, reviews, and errors should receive an explicit reply.` +
                (skipped.length ? `\nNot started: ${skipped.join("; ")}` : ""),
            }],
          };
        }

        // Include recipient status in response
        const statusParts = (r.recipients || []).map(rid => {
          const info = r.recipientStatus?.[rid];
          if (info) return `${rid} [${info.status}, ${info.unread} unread]`;
          return rid;
        });
        return {
          content: [{ type: "text", text: `Sent (${r.messageId}) to ${statusParts.join(", ")}. Subject: ${subject}` }],
        };
      }

      // -- Local mode --
      const registry = readAgents();
      if (registry.agents[from]) {
        registry.agents[from].lastSeen = new Date().toISOString();
        writeAgents(registry);
      }

      const messageId = `${Date.now()}-${randomUUID().slice(0, 8)}`;
      const message = { id: messageId, from, type, subject, body, priority: priority || "normal", inReplyTo };

      const recipients = [];
      if (to) recipients.push(to);
      if (toRole) {
        for (const [id, info] of Object.entries(registry.agents)) {
          if (info.role === toRole && id !== from) recipients.push(id);
        }
      }
      const uniqueRecipients = dedupePreserveOrder(recipients);
      if (!uniqueRecipients.length) {
        return { content: [{ type: "text", text: "No recipients found. Target may not be registered." }] };
      }

      for (const r of uniqueRecipients) deliverMessage(r, message);

      if (shouldTrigger && uniqueRecipients.length > 0) {
        const started = [];
        const skipped = [];
        for (const targetId of uniqueRecipients) {
          const targetInfo = registry.agents[targetId] || {};
          const sessionMode = normalizeSessionMode(targetInfo.sessionMode);
          const runtime = normalizeRuntime(targetInfo.runtime || "generic");
          const capabilities = Array.isArray(targetInfo.capabilities) ? targetInfo.capabilities : [];
          const residentRunnable = sessionMode === "resident" && capabilities.includes("resident-run") && targetInfo.sessionHandle;
          const managedRunnable = sessionMode === "managed" && capabilities.includes("managed-run");
          if (!residentRunnable && !managedRunnable) {
            skipped.push(
              sessionMode === "resident"
                ? `${targetId} (resident session has no triggerable session handle; re-register this live session)`
                : `${targetId} (managed session is missing launch capabilities)`,
            );
            continue;
          }
          if (!canLaunchRuntime(runtime)) {
            skipped.push(`${targetId} (${runtime})`);
            continue;
          }
          spawnTriggeredAgent({ targetId, targetInfo, from, type, subject, body });
          started.push(`${targetId} (${runtime})`);
        }
        return {
          content: [{
            type: "text",
            text:
              `Sent + triggered locally for ${started.join(", ") || "no launchable recipients"}. Reply handoff tracking is only available in remote server mode.` +
              (skipped.length ? `\nSkipped: ${skipped.join(", ")}` : ""),
          }],
        };
      }

      return {
        content: [{ type: "text", text: `Sent (${messageId}) to ${uniqueRecipients.join(", ")}. Subject: ${subject}` }],
      };
    }
  );

  server.tool(
    "comms_channel_send",
    "Send a message to a channel. This is live-delivery gated for channel members: if any recipient is offline, stale, stopped, or lacks a live wake path, the channel message is not written. Busy steer-capable members receive the channel update as steer into their active run; busy non-steer members queue or merge as next-turn work. Use queueIfBusy=true only to force next-turn delivery; when queueIfBusy=true, the steer option is ignored. Agent-reported blocked/completed states are status notes, not delivery blockers.",
    {
      channel: z.string().describe("Channel name"),
      from: z.string().describe("Your agent ID"),
      body: z.string().describe("Message content"),
      type: z
        .enum(["info", "request", "response", "error", "review", "approval"])
        .optional()
        .describe("Message type (default: info)"),
      priority: z.enum(["normal", "high", "urgent"]).optional().describe("Message priority (default: normal)"),
      steer: z.boolean().optional().describe("When true and members are busy, deliver between tool calls when supported; otherwise queue/merge as next-turn work. Defaults to true. Ignored when queueIfBusy=true."),
      queueIfBusy: z.boolean().optional().describe("When true, force this channel update behind active/queued work instead of steering active turns."),
    },
    async ({ channel, from, body, type, priority, steer, queueIfBusy }) => {
      try { validateName(channel, "channel name"); } catch (e) { return { content: [{ type: "text", text: e.message }], isError: true }; }
      const shouldTrigger = true;
      const forceQueue = queueIfBusy === true;
      const subject = `#${channel}: ${body.slice(0, 80)}`;

      if (IS_REMOTE) {
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
          return {
            content: [{
              type: "text",
              text:
                `Sent to #${channel}. Dispatch: ${queued.join(", ") || "started"}. This ack reports what was CREATED, not what was delivered -- confirm with comms_run_status(...) before reporting delivery.` +
                (skipped.length ? `\nNot started: ${skipped.join("; ")}` : ""),
            }],
          };
        }
        return { content: [{ type: "text", text: `Sent to #${channel} (${r.members.length} members).` }] };
      }

      const chFile = path.join(MESSAGES_DIR, "channels", `${channel}.json`);
      if (!fs.existsSync(chFile)) {
        return { content: [{ type: "text", text: `Channel #${channel} not found.` }], isError: true };
      }
      const ch = JSON.parse(fs.readFileSync(chFile, "utf-8"));
      if (!ch.members.includes(from)) {
        return { content: [{ type: "text", text: `Not a member of #${channel}. Join first.` }], isError: true };
      }
      const msgId = `${Date.now()}-${randomUUID().slice(0, 8)}`;
      ch.messages.push({
        id: msgId, from, type: type || "info", body, timestamp: Date.now(),
      });
      fs.writeFileSync(chFile, JSON.stringify(ch, null, 2));
      // Deliver to each member's inbox (except sender) so notifications work
      const recipients = [];
      for (const member of ch.members) {
        if (member !== from) {
          recipients.push(member);
          deliverMessage(member, {
            id: msgId, from, type: type || "info", source: "channel", channel, subject, body, priority: priority || "normal",
          });
        }
      }
      if (shouldTrigger && recipients.length > 0) {
        const started = [];
        const skipped = [];
        const registry = readAgents();
        for (const targetId of recipients) {
          const targetInfo = registry.agents[targetId] || {};
          const sessionMode = normalizeSessionMode(targetInfo.sessionMode);
          const runtime = normalizeRuntime(targetInfo.runtime || "generic");
          const capabilities = Array.isArray(targetInfo.capabilities) ? targetInfo.capabilities : [];
          const residentRunnable = sessionMode === "resident" && capabilities.includes("resident-run") && targetInfo.sessionHandle;
          const managedRunnable = sessionMode === "managed" && capabilities.includes("managed-run");
          if (!residentRunnable && !managedRunnable) {
            skipped.push(
              sessionMode === "resident"
                ? `${targetId} (resident session has no triggerable session handle; re-register that live session)`
                : `${targetId} (managed session is missing launch capabilities)`,
            );
            continue;
          }
          if (!canLaunchRuntime(runtime)) {
            skipped.push(`${targetId} (${runtime})`);
            continue;
          }
          spawnTriggeredAgent({ targetId, targetInfo, from, type: type || "info", subject, body });
          started.push(`${targetId} (${runtime})`);
        }
        return {
          content: [{
            type: "text",
            text:
              `Sent to #${channel} + triggered locally for ${started.join(", ") || "no launchable recipients"}.` +
              (skipped.length ? `\nSkipped: ${skipped.join(", ")}` : ""),
          }],
        };
      }
      return { content: [{ type: "text", text: `Sent to #${channel} (${ch.members.length} members).` }] };
    }
  );

}
