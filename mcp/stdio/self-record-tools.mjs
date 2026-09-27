// An agent writing its own record: what it is doing, and what it is for.
//
// Two MCP tools — `comms_status` records what an agent is currently working on, `comms_describe` records
// what it exists to do. v0.5.4 layer 2 of the server.js decomposition.
//
// THE EXACT COMPLEMENT of `agent-reporting-tools.mjs`, and split from it on that axis. Those two tools READ
// about agents, including other agents; these two WRITE the caller's own row and nothing else. The four
// share the same registry and it would have been easy to call them one "agents" module — but a module that
// both reports on the fleet and mutates your own record has two reasons to change, and the read side is
// where the renderers live while the write side is where validation does.
//
// WHAT THE STATUS FIELD IS AND IS NOT, because this is the surface that keeps being misread. A status
// written here is an agent's own SELF-REPORT. It is not the derived status the service computes from
// events, and it cannot be — an agent that has hung cannot update a field to say so. Anything deciding
// whether an agent is alive must use the derived status; this is a note from the agent to its team, and
// the tool descriptions carry that distinction.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { IS_REMOTE, httpCall } from "./aify-service-endpoint.mjs";
import { readAgents, writeAgents } from "./local-store.mjs";
import { validateName } from "./safe-name.mjs";

// Registers the two self-record tools on an MCP server. A function rather than a module-scope side effect,
// so a fake server can capture the registrations and a test can call the handlers without an MCP transport.
// `z` is the caller's zod — see the other tool groups for why it is not imported here.
//
// The two bodies below are the original server.js text, indented one level. Nothing else changed.
export function registerSelfRecordTools(server, z) {
  // ═══════════════════════════════════════════════════════════════════════════════
  // 2b. comms_status -- Update your agent status
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_status",
    "Set your focus note, shown beside your name on the dashboard until you change it. It does not change your status badge, which the service derives. Report finished work with a reply.",
    {
      agentId: z.string().describe("Your agent ID"),
      status: z
        .enum(["idle", "working", "reviewing", "testing", "researching", "blocked", "focused"])
        .describe("Coarse tag for the note; not your badge"),
      note: z.string().optional().describe("What you're working on, e.g. 'NRD createPipelines'"),
    },
    async ({ agentId, status, note }) => {
      try { validateName(agentId, "agent ID"); } catch (e) { return { content: [{ type: "text", text: e.message }], isError: true }; }

      if (IS_REMOTE) {
        const r = await httpCall("PATCH", `/agents/${agentId}`, { status, note });
        return { content: [{ type: "text", text: `Status updated: ${r.agentId} → ${r.status}` }] };
      }

      const registry = readAgents();
      if (!registry.agents[agentId]) {
        return { content: [{ type: "text", text: `Agent "${agentId}" not found. Register first.` }], isError: true };
      }
      registry.agents[agentId].status = note ? `${status}: ${note}` : status;
      registry.agents[agentId].lastSeen = new Date().toISOString();
      writeAgents(registry);
      return { content: [{ type: "text", text: `Status updated: ${agentId} → ${registry.agents[agentId].status}` }] };
    }
  );

  // ═══════════════════════════════════════════════════════════════════════════════
  // 2c. comms_describe -- Update your team-facing description
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_describe",
    "Set your team-facing description, shown to other agents in comms_agents until you change it.",
    {
      agentId: z.string().describe("Your agent ID"),
      description: z.string().max(2000).describe("Who you are, your project and your focus. \"\" clears it."),
    },
    async ({ agentId, description }) => {
      try { validateName(agentId, "agent ID"); } catch (e) { return { content: [{ type: "text", text: e.message }], isError: true }; }

      if (!IS_REMOTE) {
        return { content: [{ type: "text", text: "comms_describe currently requires remote server mode." }], isError: true };
      }

      try {
        const r = await httpCall("PATCH", `/agents/${encodeURIComponent(agentId)}/description`, { description });
        const preview = r.description ? `: ${r.description.slice(0, 120)}${r.description.length > 120 ? "…" : ""}` : " (cleared)";
        return { content: [{ type: "text", text: `Description updated for ${r.agentId}${preview}` }] };
      } catch (e) {
        return { content: [{ type: "text", text: `Describe error: ${e.message}` }], isError: true };
      }
    }
  );
}
