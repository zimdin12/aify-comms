// Compaction: replacing an agent's live working memory with a summary of it.
//
// One MCP tool, `comms_compact`. v0.5.4 layer 2 of the server.js decomposition moved it here; 0.7.5 gave it
// the two modes the operator specified (2026-09-26) and moved what they decide into the service.
//
// IT IS THE MOST DESTRUCTIVE NON-DELETING TOOL IN THE BRIDGE. Compaction does not remove an agent or a
// message; it removes what the agent KNEW. Anything it never wrote down is gone, so the description leads
// with that and tells a caller to have the target record open decisions somewhere durable FIRST. It stays
// out of the lifecycle group for that reason: losing working memory is not losing an identity.
//
// TWO MODES, BOTH DECIDED BY THE SERVICE (`service/api_core/compaction.py`), so this tool and the
// dashboard's Compact form cannot disagree:
//   native   `POST /agents/{id}/compact/native` types the runtime's own command (`/compact`, hermes
//            `/compress`) into a managed agent's live console, then Enter. The service refuses a resident,
//            a runtime with no verified command, a console with no TUI, and an agent that is not idle at
//            its prompt -- typed mid-turn it queues behind the turn, typed over a dialog it ANSWERS it.
//   handoff  a fresh session from a spawn request, as before. Its first message is the service's brief
//            (`GET /agents/{id}/compact/handoff-brief`): the previous session and native session id, where
//            the transcript is when the runtime's layout says, and "read your last N messages" with
//            comms_inbox. It used to paste ~24 message bodies; the inbox already holds them.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { IS_REMOTE, httpCall } from "./aify-service-endpoint.mjs";
import { AIFY_AGENT_ID } from "./launch-identity.mjs";
import { normalizeRuntime } from "./runtimes.js";
import { validateName } from "./safe-name.mjs";

function pickCompactSession(sessions = []) {
  const scores = {
    running: 100,
    starting: 90,
    recovering: 85,
    restarting: 80,
    "cli-takeover": 60,
    stopped: 40,
    lost: 25,
    failed: 20,
    ended: 5,
  };
  return [...sessions].sort((a, b) => {
    const aScore = scores[String(a.status || "").toLowerCase()] || 0;
    const bScore = scores[String(b.status || "").toLowerCase()] || 0;
    if (aScore !== bScore) return bScore - aScore;
    return (Date.parse(b.lastSeen || b.startedAt || "") || 0) - (Date.parse(a.lastSeen || a.startedAt || "") || 0);
  })[0] || null;
}

const errorText = (text) => ({ content: [{ type: "text", text }], isError: true });

async function compactNatively({ from, targetAgentId, handoff }, { call = httpCall } = {}) {
  // What a handoff reads, passed with mode=native, is refused rather than silently ignored.
  const misplaced = Object.keys(handoff).filter((name) => handoff[name] !== undefined);
  if (misplaced.length) {
    return errorText(`${misplaced.join(", ")} appl${misplaced.length === 1 ? "ies" : "y"} to mode "handoff" only; native compaction types the runtime's own command and takes none of them.`);
  }
  // The launch identity when there is one, as comms_console_input does: this writes into another agent's
  // terminal, and the audit should name who actually asked.
  const caller = AIFY_AGENT_ID || from;
  const r = await call("POST", `/agents/${encodeURIComponent(targetAgentId)}/compact/native`, { from: caller });
  if (!r.ok) return errorText(r.message || `Native compaction of "${targetAgentId}" was refused.`);
  return {
    content: [{
      type: "text",
      text: `QUEUED ${r.command} to ${targetAgentId}'s console (terminal ${r.terminalId}, control ${r.controlId}). ` +
        "Not confirmation: the bytes reach the PTY, and whether it compacted shows on its console (comms_console_tail). It keeps its session.",
    }],
  };
}

async function compactByHandoff({ from, targetAgentId, newAgentId, role, environmentId, runtime, workspace, instructions, recentMessages, priority }, { call = httpCall } = {}) {
  const agents = (await call("GET", "/agents")).agents || {};
  const targetInfo = agents[targetAgentId] || {};
  const successorId = newAgentId || targetAgentId;
  try { validateName(successorId, "handoff agent ID"); } catch (e) { return errorText(e.message); }

  const sessionsRes = await call("GET", `/sessions?agentId=${encodeURIComponent(targetAgentId)}&limit=100`);
  const sourceSession = pickCompactSession(sessionsRes.sessions || []);
  if (!sourceSession) {
    return errorText(`No managed session record found for "${targetAgentId}". Compact needs a dashboard-managed backing session. Use comms_spawn first or adopt the identity into an environment from the dashboard.`);
  }

  const query = new URLSearchParams({ sessionId: sourceSession.id || "" });
  if (recentMessages !== undefined) query.set("recentMessages", String(recentMessages));
  const brief = await call("GET", `/agents/${encodeURIComponent(targetAgentId)}/compact/handoff-brief?${query}`);
  const packet = instructions ? `${brief.text}\n\nInstructions:\n${instructions}` : brief.text;

  const resolvedRuntime = normalizeRuntime(runtime || sourceSession.runtime || targetInfo.runtime || "generic");
  const r = await call("POST", "/spawn-requests", {
    createdBy: from,
    environmentId: environmentId || sourceSession.environmentId,
    agentId: successorId,
    role: role || targetInfo.role || "coder",
    name: successorId,
    runtime: resolvedRuntime,
    workspace: workspace || sourceSession.workspace || targetInfo.cwd || "",
    initialMessage: packet,
    subject: `Handoff compact from ${targetAgentId}`,
    priority: priority || "normal",
    mode: "managed-warm",
    resumePolicy: "fresh_context",
    metadata: {
      compactMode: "handoff",
      compactedFromAgentId: targetAgentId,
      compactedFromSessionId: sourceSession.id || "",
      compactedBy: from,
      recentMessagesToRead: brief.recentMessages,
      sameAgentId: successorId === targetAgentId,
    },
  });
  const req = r.spawnRequest || {};
  const identityText = successorId === targetAgentId ? `same agent ID "${successorId}"` : `successor "${successorId}"`;
  return {
    content: [{
      type: "text",
      text:
        `Queued handoff compaction for ${identityText} from "${targetAgentId}". Spawn request: ${req.id || "unknown"} [${req.status || "queued"}]. ` +
        `The fresh session is told its previous session and to read its last ${brief.recentMessages} message(s); the old native session is not reused.`,
    }],
  };
}

/** The tool's handler: every parameter named here, then handed to the mode that reads it. */
async function commsCompactHandler(args) {
  const { from, targetAgentId, mode, newAgentId, role, environmentId, runtime, workspace, instructions, recentMessages, priority } = args;
  if (!IS_REMOTE) {
    return errorText("Managed compaction requires remote server mode. Set AIFY_SERVER_URL to the service, or re-run install.sh with its URL, then restart this agent.");
  }
  try {
    validateName(from, "from agent ID");
    validateName(targetAgentId, "target agent ID");
  } catch (e) {
    return errorText(e.message);
  }
  const handoff = { newAgentId, role, environmentId, runtime, workspace, instructions, recentMessages, priority };
  try {
    return mode === "native"
      ? await compactNatively({ from, targetAgentId, handoff })
      : await compactByHandoff({ from, targetAgentId, ...handoff });
  } catch (error) {
    return errorText(error.message);
  }
}

// Registers the compaction tool. A function rather than a module-scope side effect, so a fake server can
// capture the registration and a test can call the handler without an MCP transport. `z` is the caller's zod
// -- see the other tool groups for why it is not imported here.
export function registerCompactTool(server, z) {
  server.tool(
    "comms_compact",
    // Every agent re-reads this on every turn. What stays is what changes the CALLER's action, and what
    // the contracts pin: `/DESTRUCTIVE TO CONTEXT/` and `/record open decisions somewhere durable FIRST/`.
    // Which fields belong to which mode is said once, by the `mode` field and a "handoff:" prefix.
    "DESTRUCTIVE TO CONTEXT — the target continues from a summary: whatever it knew but never wrote down is gone. " +
      "Use when a managed agent is degraded by a long noisy session, not as routine hygiene. " +
      "Have it record open decisions somewhere durable FIRST.",
    {
      from: z.string().describe("Manager/coordinator agent requesting the compact"),
      targetAgentId: z.string().describe("Existing managed agent to compact"),
      mode: z.enum(["handoff", "native"]).optional().describe(
        "handoff (default): fresh session, told its old session id and to read its recent messages. " +
        "native: types its runtime's /compact (hermes /compress) into its live console; managed, idle agents only.",
      ),
      newAgentId: z.string().optional().describe("handoff: a separate continuation identity. Default: the target's own."),
      role: z.string().optional().describe("handoff: default the target's role, else coder."),
      environmentId: z.string().optional().describe("handoff: default the source session's."),
      runtime: z.string().optional().describe("handoff: default the source session's."),
      workspace: z.string().optional().describe("handoff: default the source session's."),
      instructions: z.string().optional().describe("handoff: a phase brief for the fresh session."),
      recentMessages: z.number().int().min(0).optional().describe("handoff: messages it is told to read. Default 10."),
      priority: z.enum(["normal", "high", "urgent"]).optional().describe("handoff: priority of its brief."),
    },
    (args) => commsCompactHandler(args)
  );
}
