// Stopping, retiring and resetting: the destructive end of an agent's life.
//
// Four MCP tools — `comms_remove_agent`, `comms_delete_session`, `comms_restart`, `comms_clear`.
// v0.5.4 layer 2 of the server.js decomposition, the fourth tool group to move.
//
// WHAT MAKES THEM ONE SUBJECT, since "destructive" alone would be a bag. Three of the four mutate the
// state that `bridge-agent-state.mjs` owns: `comms_restart` clears all three of its Maps, `comms_clear`
// clears them or forgets one agent, and `comms_remove_agent` calls `forgetRemoteAgent`. They are the
// write side of the forget invariant, and the group could not be cut at all until that state had an
// owner. `comms_delete_session` removes one runtime session record and is here because the other three
// name it: restart's own description tells a caller to prefer it over `delete_session`+send.
//
// THEIR DESCRIPTIONS CROSS-REFERENCE EACH OTHER AND NOTHING OUTSIDE THE SET, which is the check that kept
// the boundary honest. `comms_remove_agent` says it is NOT for restarting a stuck one and names
// `comms_restart`; `comms_restart` names `delete_session`. None of them names `comms_compact`, which is
// why compaction — losing working memory, not losing an identity — stayed out despite also being
// destructive and also being blocked at the time.
//
// THE BLAST RADII DIFFER BY ORDERS OF MAGNITUDE and the descriptions are the only thing that says so.
// `comms_delete_session` drops one inactive record. `comms_remove_agent` tombstones one identity.
// `comms_clear` with target="all" wipes every message, artifact and identity on the server — other teams
// included, with no undo and no confirmation prompt. The tests below assert those warnings survive,
// because for `comms_clear` the description IS the safety mechanism.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import fs from "fs";
import path from "path";

import { IS_REMOTE, httpCall } from "./aify-service-endpoint.mjs";
import {
  ACTIVE_RUNS, CONSECUTIVE_FAILURES, REMOTE_AGENT_STATE, forgetRemoteAgent,
} from "./bridge-agent-state.mjs";
import { INBOX_DIR, SHARED_DIR, readAgents, writeAgents } from "./local-store.mjs";
import { validateName } from "./safe-name.mjs";

// Registers the four lifecycle tools on an MCP server. A function rather than a module-scope side
// effect, so a fake server can capture the registrations and a test can call the handlers without an MCP
// transport. `z` is the caller's zod — see the other tool groups for why it is not imported here.
//
// The four bodies below are the original server.js text, indented one level. Nothing else changed.
export function registerLifecycleTools(server, z) {
  // ═══════════════════════════════════════════════════════════════════════════════
  // 14. comms_remove_agent -- Remove one agent identity
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_remove_agent",
    "DESTRUCTIVE. Tombstones one agent identity: unregisters the ID and stops this bridge from auto-re-registering it. " +
      "Their message history survives, but the identity stops being addressable and a live session under it is orphaned. " +
      "This is for retiring an agent for good — NOT for restarting a stuck one (comms_restart), stopping one temporarily " +
      "(dashboard Sessions), or clearing an inbox (comms_clear with agentId). Re-creating the same ID later is a fresh identity, not a restore.",
    {
      agentId: z.string().describe("Agent ID to remove"),
    },
    async ({ agentId }) => {
      try { validateName(agentId, "agent ID"); } catch (e) { return { content: [{ type: "text", text: e.message }], isError: true }; }

      if (IS_REMOTE) {
        const r = await httpCall("DELETE", `/agents/${encodeURIComponent(agentId)}`);
        forgetRemoteAgent(agentId);
        return {
          content: [{
            type: "text",
            text: r.ok ? `Removed agent "${agentId}".` : `Agent "${agentId}" was already absent; future auto re-registration is blocked until explicit register.`,
          }],
        };
      }

      const registry = readAgents();
      const existed = Boolean(registry.agents?.[agentId]);
      if (registry.agents) delete registry.agents[agentId];
      writeAgents(registry);
      return { content: [{ type: "text", text: existed ? `Removed agent "${agentId}".` : `Agent "${agentId}" was not registered.` }] };
    }
  );

  // ═══════════════════════════════════════════════════════════════════════════════
  // 15. comms_delete_session -- Delete one inactive runtime session record
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_delete_session",
    "Delete one inactive runtime session record. Active/running sessions must be stopped first. This does not delete the agent identity or chat messages.",
    {
      sessionId: z.string().describe("Runtime session ID to delete"),
    },
    async ({ sessionId }) => {
      const id = String(sessionId || "").trim();
      if (!id) {
        return { content: [{ type: "text", text: "sessionId is required." }], isError: true };
      }
      if (!IS_REMOTE) {
        return {
          content: [{ type: "text", text: "comms_delete_session requires the HTTP-backed aify-comms service; local filesystem mode has no runtime session table." }],
          isError: true,
        };
      }
      try {
        const r = await httpCall("DELETE", `/sessions/${encodeURIComponent(id)}`);
        return {
          content: [{
            type: "text",
            text: r.ok
              ? `Deleted inactive session "${id}" for agent "${r.agentId || "unknown"}".`
              : `Session "${id}" was not deleted.`,
          }],
        };
      } catch (error) {
        return { content: [{ type: "text", text: error?.message || "Failed to delete session." }], isError: true };
      }
    }
  );

  // ═══════════════════════════════════════════════════════════════════════════════
  // 15b. comms_restart -- Gracefully restart a MANAGED agent's session (dashboard-equivalent)
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_restart",
    "Gracefully restart another agent's MANAGED session — the same safe path as the dashboard's Sessions -> Restart: it stops the live worker and re-spawns it via the environment bridge, keeping the agent's native session/context. Set freshContext=true for a Reset (discards the native session and starts clean, = the dashboard 'Reset' button). Only works on session_mode='managed' agents: RESIDENT sessions are operator-owned and CANNOT be restarted remotely (a session-restart on a live resident would fork a managed twin) — use comms_run_interrupt to stop its current run, or ask the operator to relaunch. Prefer this over delete_session+send: it is the graceful, dashboard-equivalent recreate.",
    {
      agentId: z.string().describe("Agent whose managed session to restart"),
      freshContext: z.boolean().optional().describe("true = Reset (discard native session, fresh context); false/omitted = Restart (keep native session)"),
    },
    async ({ agentId, freshContext }) => {
      const id = String(agentId || "").trim();
      if (!id) return { content: [{ type: "text", text: "agentId is required." }], isError: true };
      if (!IS_REMOTE) {
        return { content: [{ type: "text", text: "comms_restart requires the HTTP-backed aify-comms service; there is no managed session to restart in local filesystem mode." }], isError: true };
      }
      try {
        const info = await httpCall("GET", `/agents/${encodeURIComponent(id)}`);
        const agent = info.agent || info;
        const mode = String(agent.sessionMode || agent.session_mode || "").toLowerCase();
        if (!mode) return { content: [{ type: "text", text: `Agent "${id}" not found or has no session.` }], isError: true };
        if (mode !== "managed") {
          return {
            content: [{ type: "text", text: `Agent "${id}" is ${mode}, not managed. Resident sessions are operator-owned and can't be restarted remotely — ask the operator to relaunch it, or use comms_run_interrupt to stop its current run.` }],
            isError: true,
          };
        }
        const list = await httpCall("GET", `/sessions?agentId=${encodeURIComponent(id)}`);
        const sessions = Array.isArray(list.sessions) ? list.sessions : [];
        const live = new Set(["starting", "running", "recovering", "restarting", "cli-takeover"]);
        const target = sessions.find((s) => live.has(String(s.status || "").toLowerCase())) || sessions[0];
        if (!target || !target.id) {
          return { content: [{ type: "text", text: `Agent "${id}" has no session to restart — send it a message instead; a managed agent cold-starts a fresh worker on send.` }], isError: true };
        }
        const action = freshContext ? "recreate" : "restart";
        const r = await httpCall("POST", `/sessions/${encodeURIComponent(target.id)}/control`, {
          action,
          from_agent: process.env.AIFY_AGENT_ID || "agent",
        });
        const verb = freshContext ? "reset with a fresh context" : "restarted";
        return {
          content: [{
            type: "text",
            text: r && r.ok === false
              ? `Restart of "${id}" was not accepted: ${r.error || "unknown reason"}.`
              : `Managed session for "${id}" ${verb} (session ${target.id}, action=${action}); it re-spawns via the environment bridge.`,
          }],
        };
      } catch (error) {
        return { content: [{ type: "text", text: error?.message || "Failed to restart agent." }], isError: true };
      }
    }
  );

  // ═══════════════════════════════════════════════════════════════════════════════
  // 16. comms_clear -- Clear inbox/shared/agents/all with optional age filter
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_clear",
    "DESTRUCTIVE AND IRREVERSIBLE. Permanently deletes data for the WHOLE hub, not just for you. " +
      "target=\"all\" wipes every message, shared artifact and agent identity on the server — other teams included. " +
      "There is no undo and no confirmation prompt; the only safety is this sentence. " +
      "Do NOT use it to tidy your own inbox (messages are auto-marked read; just leave them) or to remove one agent (use comms_remove_agent). " +
      "Scope it as narrowly as the task allows: pass agentId, and prefer olderThanHours over a bare wipe. " +
      "If you did not explicitly decide to destroy shared history, you want a different tool.",
    {
      target: z.enum(["inbox", "shared", "agents", "all"]).describe("What to clear"),
      agentId: z.string().optional().describe("Limit to one agent for target=inbox or target=agents"),
      olderThanHours: z.number().optional().describe("Only clear items older than N hours"),
    },
    async ({ target, agentId, olderThanHours }) => {
      if (IS_REMOTE) {
        const r = await httpCall("POST", "/clear", { target, agentId, olderThanHours });
        if (target === "agents" && agentId) {
          forgetRemoteAgent(agentId);
        } else if (target === "agents" || target === "all") {
          REMOTE_AGENT_STATE.clear();
          ACTIVE_RUNS.clear();
          CONSECUTIVE_FAILURES.clear();
        }
        const c = r.cleared || {};
        const parts = [];
        if (c.messages) parts.push(`${c.messages} messages`);
        if (c.files) parts.push(`${c.files} files`);
        if (c.agents) parts.push(`${c.agents} agents`);
        return { content: [{ type: "text", text: parts.length ? `Cleared: ${parts.join(", ")}.` : "Nothing to clear." }] };
      }

      const cutoff = olderThanHours ? Date.now() - olderThanHours * 3600_000 : Infinity;
      const cleared = { messages: 0, files: 0, agents: 0 };

      // Clear inbox
      if (target === "inbox" || target === "all") {
        const dirs = agentId
          ? [agentId]
          : (() => { try { return fs.readdirSync(INBOX_DIR); } catch { return []; } })();

        for (const dir of dirs) {
          const dirPath = path.join(INBOX_DIR, dir);
          try {
            for (const f of fs.readdirSync(dirPath).filter((f) => f.endsWith(".json"))) {
              const filePath = path.join(dirPath, f);
              if (cutoff < Infinity) {
                try {
                  const msg = JSON.parse(fs.readFileSync(filePath, "utf-8"));
                  if (msg.timestamp > cutoff) continue;
                } catch { /* delete anyway */ }
              }
              fs.unlinkSync(filePath);
              cleared.messages++;
            }
          } catch { /* dir doesn't exist */ }
        }
      }

      // Clear shared files
      if (target === "shared" || target === "all") {
        try {
          for (const f of fs.readdirSync(SHARED_DIR)) {
            const filePath = path.join(SHARED_DIR, f);
            if (cutoff < Infinity) {
              try {
                if (fs.statSync(filePath).mtimeMs > cutoff) continue;
              } catch { /* delete anyway */ }
            }
            fs.unlinkSync(filePath);
            cleared.files++;
          }
        } catch { /* dir doesn't exist */ }
      }

      // Clear agent registry
      if (target === "agents" || target === "all") {
        const registry = readAgents();
        if (agentId && target === "agents") {
          if (registry.agents?.[agentId]) {
            delete registry.agents[agentId];
            cleared.agents = 1;
          }
          writeAgents(registry);
        } else {
          cleared.agents = Object.keys(registry.agents).length;
          writeAgents({ agents: {} });
        }
      }

      const parts = [];
      if (cleared.messages) parts.push(`${cleared.messages} messages`);
      if (cleared.files) parts.push(`${cleared.files} shared files`);
      if (cleared.agents) parts.push(`${cleared.agents} agents`);
      return {
        content: [{ type: "text", text: parts.length ? `Cleared: ${parts.join(", ")}.` : "Nothing to clear." }],
      };
    }
  );
}
