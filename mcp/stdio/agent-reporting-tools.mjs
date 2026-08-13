// Reporting on agents: the fleet, and one agent in detail.
//
// Two MCP tools — `comms_agents` lists who exists and what state they are in, `comms_agent_info` answers
// the same questions about one agent with more depth. v0.5.4 layer 2 of the server.js decomposition.
//
// THEY SHARE FOUR RENDERERS AND NOTHING ELSE DOES, which is what makes them one group rather than two
// adjacent tools: `runtimeSummary`, `wakeModeSummary`, `formatDispatchState` and `formatOutboundActivity`
// are each read by exactly these two. Split, the same fleet would be described in two voices — and the
// wake-path answer in particular is the one an operator uses to decide whether a silent agent is idle or
// unreachable, so the two views disagreeing about it is the failure that matters.
//
// WHAT THESE TOOLS ARE FOR IS NARROWER THAN THEY LOOK, and the wording carries a hard-won distinction.
// `comms_agent_info` labels its inbound facts as inbound — unread count, last seen, last read — because
// every one of them was individually TRUE during the 2026-08-10 outage while a reply sat undelivered, and
// a manager read them three times as evidence the lane was dead. "Unread: 0" is a statement about what an
// agent has not READ, never about whether it has produced anything. The outbound line exists to answer the
// question people were actually asking, and the tests below assert both halves stay labelled.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { IS_REMOTE, httpCall } from "./aify-service-endpoint.mjs";
import { runtimeSummary, wakeModeSummary } from "./agent-summary.mjs";
import { readAgents, readInbox } from "./local-store.mjs";
import { formatDispatchState, formatOutboundActivity } from "./tool-response-format.mjs";

// Registers the two agent-reporting tools on an MCP server. A function rather than a module-scope side
// effect, so a fake server can capture the registrations and a test can call the handlers without an MCP
// transport. `z` is the caller's zod — see the other tool groups for why it is not imported here.
//
// The two bodies below are the original server.js text, indented one level. Nothing else changed.
export function registerAgentReportingTools(server, z) {
  // ═══════════════════════════════════════════════════════════════════════════════
  // 2. comms_agents -- List all agents with unread counts
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_agents",
    "List all registered agents, their roles, and unread message counts.",
    {},
    async () => {
      const describeLine = (info) => {
        const desc = String(info.description || "").trim();
        if (!desc) return "";
        const preview = desc.length > 160 ? `${desc.slice(0, 159)}…` : desc;
        return `\n    ${preview}`;
      };
      if (IS_REMOTE) {
        const r = await httpCall("GET", "/agents");
        const entries = Object.entries(r.agents || {});
        if (!entries.length) return { content: [{ type: "text", text: "No agents registered." }] };
        const lines = entries.map(([id, info]) => {
          const status = info.status ? ` [${info.status}]` : "";
          return `- ${id} (${info.role})${status} -- "${info.name}" | ${runtimeSummary(info)} | wake: ${wakeModeSummary(info)} | unread: ${info.unread || 0} | last seen: ${info.lastSeen}${describeLine(info)}`;
        });
        return { content: [{ type: "text", text: lines.join("\n") }] };
      }

      const registry = readAgents();
      const entries = Object.entries(registry.agents);
      if (!entries.length) return { content: [{ type: "text", text: "No agents registered." }] };
      const lines = entries.map(([id, info]) => {
        const unread = readInbox(id, "unread").length;
        const status = info.status ? ` [${info.status}]` : "";
        return `- ${id} (${info.role})${status} -- "${info.name}" | ${runtimeSummary(info)} | wake: ${wakeModeSummary(info)} | unread: ${unread} | last seen: ${info.lastSeen}${describeLine(info)}`;
      });
      return { content: [{ type: "text", text: lines.join("\n") }] };
    }
  );

  // ═══════════════════════════════════════════════════════════════════════════════
  // 5b. comms_agent_info -- Check another agent's status and last read message
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_agent_info",
    "Check another agent's current status, unread count, and last message they read. " +
      "Useful for knowing if they've seen your message.",
    {
      agentId: z.string().describe("Agent ID to check"),
    },
    async ({ agentId }) => {
      if (IS_REMOTE) {
        try {
          const agents = await httpCall("GET", "/agents");
          const info = agents.agents?.[agentId];
          if (!info) return { content: [{ type: "text", text: `Agent "${agentId}" not found.` }], isError: true };

          let lastRead = "unknown";
          try {
            const lr = await httpCall("GET", `/agents/${agentId}/last-read`);
            if (lr.lastRead) {
              lastRead = `"${lr.lastRead.subject}" from ${lr.lastRead.from} (read at ${lr.lastRead.readAt})`;
            } else {
              lastRead = "no messages read yet";
            }
          } catch { /* best effort */ }

          return { content: [{ type: "text", text:
            `${agentId} (${info.role}) [${info.status}]\n` +
            `  Runtime: ${runtimeSummary(info)}\n` +
            `  Wake mode: ${wakeModeSummary(info)}\n` +
            // INBOUND facts, labelled as such. Every one of these was individually true during the
            // 2026-08-10 outage while a reply sat undelivered, and a manager read them as evidence
            // the lane was dead — three times. "Unread: 0" is about what this agent has not READ.
            `  Unread (inbound): ${info.unread}\n` +
            `  Last seen (registration liveness, not output): ${info.lastSeen}\n` +
            `  Last read (inbound): ${lastRead}\n` +
            // OUTBOUND — the question everyone was actually asking, and the one nothing answered.
            `  ${formatOutboundActivity(info)}` +
            (formatDispatchState(info) ? `\n${formatDispatchState(info)}` : "")
          }] };
        } catch (e) {
          return { content: [{ type: "text", text: `Error: ${e.message}` }], isError: true };
        }
      }

      // Local mode
      const registry = readAgents();
      const info = registry.agents[agentId];
      if (!info) return { content: [{ type: "text", text: `Agent "${agentId}" not found.` }], isError: true };
      const unread = readInbox(agentId, "unread").length;
      return { content: [{ type: "text", text:
        `${agentId} (${info.role}) [${info.status || "idle"}]\n` +
        `  Runtime: ${runtimeSummary(info)}\n` +
        `  Wake mode: ${wakeModeSummary(info)}\n` +
        `  Unread: ${unread}\n` +
        `  Last seen: ${info.lastSeen}`
      }] };
    }
  );
}
