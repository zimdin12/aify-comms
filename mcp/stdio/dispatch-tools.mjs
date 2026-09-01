// Dispatch: sending work to another agent and following what happens to it.
//
// Five MCP tools — `comms_dispatch`, `comms_run_status`, `comms_contracts`, `comms_run_interrupt`,
// `comms_interrupt` — plus the two helpers only they use. v0.5.4 layer 2 of the server.js
// decomposition, and the first tool group to move.
//
// WHY THIS GROUP WENT FIRST. It was measurable. Its closure is exactly two helpers, both
// group-exclusive; it needs no module state; and neither `comms_register` nor `runDispatchLoop` is
// reachable from it — those two carry a 53-function closure between them and were excluded on that
// basis before this slice began.
//
// THE DEPENDENCY LIST WAS WRONG THE FIRST TIME, WHICH IS THE USEFUL PART. This group was measured as
// depending on `IS_REMOTE` and `AIFY_AGENT_ID`, and an attempt to move it under that assumption was
// reverted: `commsInterruptHandler` must be a module-level export to be testable, and a module-level
// export cannot see a wrapper function's parameter. The premise was the error. Both names have 50+
// readers across the bridge and are properties of the PROCESS, not of this group — they now have
// owners (`aify-service-endpoint.mjs`, `launch-identity.mjs`) and are imported below like anything
// else. A name a group reads is not thereby a name the group owns; count the readers outside it.
//
// WHY `z` IS A PARAMETER AND NOT AN IMPORT, which looks like an inconsistency and is not. server.js
// loads zod through `await import("zod")` placed deliberately BELOW its `AIFY_BRIDGE_DISABLED`
// early-exit: in RPC-child mode the bridge exits before the MCP SDK or zod is ever loaded. A static
// `import { z } from "zod"` here would be hoisted above that guard by every importer and quietly undo
// it. `rpc-child-bridge-disabled.test.js` is what protects that, and it should keep passing for the
// reason it was written rather than by luck.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially, never in parallel) AND every
// wrapper relaunches. Running bridges keep executing the copy they loaded at boot.

import { IS_REMOTE, httpCall } from "./aify-service-endpoint.mjs";
import { AIFY_AGENT_ID } from "./launch-identity.mjs";
import { formatQueuedRun, replyExpectationSummary } from "./tool-response-format.mjs";

// PRIVATE, and byte-identical to the server.js original including its lack of an export.
//
// It briefly carried an `export` here, on the standard that an extracted module should export what it
// extracts. The reviewer overruled that for group-private helpers: the module exports its OWNER SURFACE
// — `registerDispatchTools` — and a helper with no consumer outside the group stays inside it. A test
// is not a consumer that justifies widening a module's API; the wording this produces is asserted
// through `comms_contracts`, which is the only thing that calls it.
function summarizeContract(contract = {}) {
  const route = `${contract.from || "?"} -> ${contract.targetAgentId || "?"}`;
  const state = String(contract.state || "sent").replace(/_/g, " ");
  const subject = contract.subject || contract.id || "(no subject)";
  const age = Number(contract.ageMinutes || 0);
  const ageText = Number.isFinite(age) ? (age >= 60 ? `${Math.round(age / 6) / 10}h` : `${Math.round(age)}m`) : "?";
  const reminders = contract.reminderCount ? `, reminders=${contract.reminderCount}` : "";
  const reply = contract.resultPreview ? `\n  answer: ${String(contract.resultPreview).slice(0, 180)}` : "";
  return `- ${state.toUpperCase()} ${route} (${ageText}${reminders}) ${subject}${reply}`;
}

export async function commsInterruptHandler({ agentId, from }, { httpCall: call = httpCall } = {}) {
  if (!IS_REMOTE) {
    return { content: [{ type: "text", text: "Agent interrupt is only available in remote server mode." }], isError: true };
  }
  try {
    const r = await call("POST", `/agents/${encodeURIComponent(agentId)}/console/input`, {
      text: "\u0003",
      enter: false,
      from: from || AIFY_AGENT_ID || "",
    });
    if (!r.ok) {
      return { content: [{ type: "text", text: r.message || `Could not interrupt ${agentId}.` }], isError: true };
    }
    return {
      content: [{ type: "text", text: `Interrupted ${agentId} through its live console (terminal ${r.terminalId}, control ${r.controlId}).` }],
    };
  } catch (error) {
    return { content: [{ type: "text", text: error.message }], isError: true };
  }
}

// Registers the five dispatch tools on an MCP server.
//
// A FUNCTION AND NOT MODULE-SCOPE SIDE EFFECT, deliberately. Registration at import time would fire on
// any import, including a test's — which is precisely how the tools become testable: a fake server
// object records what was registered, and the handlers can then be called directly without an MCP
// transport, a child process, or a live service.
//
// `z` is the caller's zod; see the note at the top of this file for why it is not imported here.
//
// The five bodies below are the original server.js text, indented one level to sit inside this
// function. Nothing else about them changed.
export function registerDispatchTools(server, z) {
  server.tool(
    "comms_dispatch",
    "Lower-level run-control/debug API for a triggerable resident or environment-managed session. Normal agent teamwork should use comms_send, which already fails fast for unreachable targets and handles busy targets with steer or queue/merge. Use comms_dispatch only when you need explicit run-control fields while diagnosing delivery/runtime behavior. Same reply contract as comms_send: when this opens a run that owes a reply, answer with comms_send(type=\"response\", inReplyTo=<the message id>) in the SAME turn — that tool call is the team-visible reply and closes the run; your final plain text is your own working output, not the delivered reply.",
    {
      from: z.string().describe("Your agent ID"),
      to: z.string().optional().describe("Target agent ID"),
      toRole: z.string().optional().describe("Send to all agents with this role"),
      type: z
        .enum(["request", "response", "info", "error", "review", "approval"])
        .describe("Message type"),
      subject: z.string().describe("Short subject"),
      body: z.string().describe("Task details"),
      priority: z.enum(["normal", "high", "urgent"]).optional().describe("Message priority (default: normal)"),
      inReplyTo: z.string().optional().describe("Message ID this replies to"),
      requireStart: z.boolean().optional().describe("Legacy strict-start flag. Current normal live delivery already fails instead of queueing future work; leave unset unless debugging old clients."),
      requireReply: z.boolean().optional().describe("Advanced override for reply tracking; normal requests/reviews/errors should be answered explicitly"),
    },
    async ({ from, to, toRole, type, subject, body, priority, inReplyTo, requireStart, requireReply }) => {
      if (!to && !toRole) {
        return { content: [{ type: "text", text: "Error: need 'to' or 'toRole'" }], isError: true };
      }

      if (!IS_REMOTE) {
        return {
          content: [{ type: "text", text: "comms_dispatch currently requires remote server mode. Use comms_send(...) in local mode." }],
          isError: true,
        };
      }

      const r = await httpCall("POST", "/dispatch", {
        from_agent: from,
        to,
        toRole,
        type,
        subject,
        body,
        priority: priority || "normal",
        inReplyTo,
        mode: requireStart ? "require_start" : "start_if_possible",
        createMessage: true,
        requireReply,
      });

      if (!r.ok) {
        return { content: [{ type: "text", text: r.error || "Dispatch failed." }], isError: true };
      }

      const lines = (r.runs || []).map((run) => {
        return `- ${formatQueuedRun(run)} [${run.status}]`;
      });
      const skipped = (r.notStarted || []).map((item) => `- ${item.targetAgentId}: ${item.reason}`);
      const footer = requireStart
        ? "\n\nUse comms_run_status(...) to inspect progress. For normal teamwork messages outside a delivered managed run, prefer comms_send(...); it already fails visibly when live delivery is not possible."
        : "\n\nUse comms_run_status(...) to inspect progress. Explicit replies are expected by default for direct dispatch; if none is sent, the bridge mirrors the run result back.";
      return {
        content: [{
          type: "text",
          text:
            `Dispatch handling:\n${lines.join("\n") || "- none"}` +
            (skipped.length ? `\n\nNot started:\n${skipped.join("\n")}` : "") +
            footer,
        }],
      };
    }
  );

  server.tool(
    "comms_run_status",
    "Inspect a dispatched run: its status, recent events, and any control requests against it.",
    {
      runId: z.string().describe("Dispatch run ID"),
    },
    async ({ runId }) => {
      if (!IS_REMOTE) {
        return { content: [{ type: "text", text: "Run status is only available in remote server mode." }], isError: true };
      }

      const r = await httpCall("GET", `/dispatch/runs/${encodeURIComponent(runId)}`);
      const run = r.run;
      const events = (run.events || []).slice(-10).map((event) => `- ${event.createdAt} [${event.type}] ${event.body || ""}`);
      const controls = (run.controls || []).slice(-10).map((control) =>
        `- ${control.requestedAt} [${control.action}/${control.status}] ${control.from || "unknown"}${control.response ? ` -> ${control.response}` : ""}`
      );
      return {
        content: [{
          type: "text",
          text:
            `${run.id} -> ${run.targetAgentId}\n` +
            `Status: ${run.status}\n` +
            `Reply: ${replyExpectationSummary(run)}\n` +
            `Runtime: ${run.runtime || "unknown"}\n` +
            `Subject: ${run.subject}\n` +
            `Requested: ${run.requestedAt}\n` +
            (run.startedAt ? `Started: ${run.startedAt}\n` : "") +
            (run.finishedAt ? `Finished: ${run.finishedAt}\n` : "") +
            (run.blockedByActiveRun?.runId ? `Blocked by active run: ${run.blockedByActiveRun.runId}${run.blockedByActiveRun.subject ? ` (${run.blockedByActiveRun.subject})` : ""}\n` : "") +
            (run.externalThreadId ? `Thread: ${run.externalThreadId}\n` : "") +
            (run.externalTurnId ? `Turn: ${run.externalTurnId}\n` : "") +
            (run.summary ? `\nSummary:\n${run.summary}\n` : "") +
            (run.error ? `\nError:\n${run.error}\n` : "") +
            (events.length ? `\nRecent events:\n${events.join("\n")}` : "") +
            (controls.length ? `\nRecent controls:\n${controls.join("\n")}` : ""),
        }],
      };
    }
  );

  server.tool(
    "comms_contracts",
    "List reply/work contracts derived from messages and dispatch runs. Use this to see who owes whom a reply, what is overdue, and whether unread counts are real work or old noise.",
    {
      agentId: z.string().optional().describe("Show contracts targeting this agent"),
      from: z.string().optional().describe("Show contracts created by this sender"),
      state: z.enum(["open", "overdue", "working", "queued", "seen", "sent", "missing_reply", "failed", "answered", "closed"]).optional().describe("Filter by computed contract state. Defaults to open."),
      category: z.enum(["direct", "channel", "self_wake"]).optional().describe("Filter by category. Defaults to direct so old channel fan-out does not hide owned work."),
      includeClosed: z.boolean().optional().describe("Include answered/closed recent contracts. Default false."),
      limit: z.number().int().min(1).max(200).optional().describe("Max contracts to return. Default 25."),
    },
    async ({ agentId, from, state, category, includeClosed, limit }) => {
      if (!IS_REMOTE) {
        return { content: [{ type: "text", text: "Work contracts require remote server mode." }], isError: true };
      }
      const params = new URLSearchParams();
      if (agentId) params.set("agentId", agentId);
      if (from) params.set("fromAgent", from);
      params.set("state", state || "open");
      params.set("category", category || "direct");
      if (includeClosed) params.set("includeClosed", "true");
      params.set("limit", String(limit || 25));
      const r = await httpCall("GET", `/contracts?${params.toString()}`);
      const contracts = r.contracts || [];
      const summary = r.summary || {};
      const header =
        `Contracts: ${summary.total || contracts.length}; open=${summary.open || 0}; overdue=${summary.overdue || 0}; ` +
        `working=${summary.working || 0}; queued=${summary.queued || 0}; missingReply=${summary.missingReply || 0}; answered=${summary.answered || 0}`;
      const body = contracts.length ? contracts.map(summarizeContract).join("\n") : "No matching contracts.";
      return { content: [{ type: "text", text: `${header}\n${body}` }] };
    }
  );

  server.tool(
    "comms_run_interrupt",
    "Request interruption of an active dispatched RUN. Returns a control request ID. No dispatched run to interrupt? Use comms_interrupt.",
    {
      runId: z.string().describe("Dispatch run ID"),
      from: z.string().optional().describe("Requesting agent ID"),
    },
    async ({ runId, from }) => {
      if (!IS_REMOTE) {
        return { content: [{ type: "text", text: "Run control is only available in remote server mode." }], isError: true };
      }
      try {
        const r = await httpCall("POST", `/dispatch/runs/${encodeURIComponent(runId)}/control`, {
          from_agent: from || "",
          action: "interrupt",
        });
        return {
          content: [{ type: "text", text: `Interrupt requested for ${runId}. Control ID: ${r.controlId}` }],
        };
      } catch (error) {
        return { content: [{ type: "text", text: error.message }], isError: true };
      }
    }
  );

  server.tool(
    "comms_interrupt",
    "Interrupt the agent currently running in a managed CONSOLE. Sends terminal-native Ctrl+C, so it also works for turns started directly in the TUI rather than by a dispatch run. For a dispatched run, comms_run_interrupt is the tracked path.",
    {
      agentId: z.string().describe("Target agent ID"),
      from: z.string().optional().describe("Requesting agent ID"),
    },
    (args) => commsInterruptHandler(args),
  );
}
