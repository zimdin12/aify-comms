// Compaction: replacing an agent's live working memory with a summary of it.
//
// One MCP tool, `comms_compact`, and the four helpers only it uses. v0.5.4 layer 2 of the server.js
// decomposition — the last tool group with no unowned dependency, and the last one that could be cut
// without resolving a seam packet.
//
// IT IS THE MOST DESTRUCTIVE NON-DELETING TOOL IN THE BRIDGE, which is why the four helpers travel with it
// rather than being generalised. Compaction does not remove an agent or a message; it removes what the agent
// KNEW. Anything it never wrote down is gone, and nothing can recover it — so the tool's own description
// leads with that and tells a caller to have the target record open decisions somewhere durable FIRST.
// Reviewer's ruling kept it out of the lifecycle group for exactly this reason: losing working memory is not
// losing an identity, and the two are different subjects even though both are destructive.
//
// TWO MODES, AND ONLY ONE OF THEM WORKS TODAY. `handoff` builds a portable packet and spawns a fresh managed
// backing from it — reliable, and the default. `internal` asks the runtime to compact in place, and
// currently returns unsupported unless an adapter proves native support; `internalCompactUnsupportedText`
// is that refusal. It is a real branch rather than dead code because adapters are expected to grow the
// capability, and the refusal names the session and runtime so an operator can tell which.
//
// `internalCompactUnsupportedText` BRIEFLY LIVED IN `tool-response-format.mjs` and was returned. It calls
// `normalizeRuntime`, an import, so it is not a pure formatter — that module's header records the episode
// and the scan hole that let it through. Here it is what it always was: a helper of this group.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { IS_REMOTE, httpCall } from "./aify-service-endpoint.mjs";
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

function messageContextForCompact(messages = [], targetAgentId, count = 24) {
  return messages
    .filter((message) => {
      if (message.source === "channel") return message.from === targetAgentId;
      return message.from === targetAgentId || message.to === targetAgentId;
    })
    .sort((a, b) => (Date.parse(b.timestamp || "") || 0) - (Date.parse(a.timestamp || "") || 0))
    .slice(0, Math.max(0, Number(count || 0)))
    .reverse()
    .map((message) => ({
      timestamp: message.timestamp || "",
      route: message.source === "channel"
        ? `${message.from || ""} -> #${message.channel || ""}`
        : `${message.from || ""} -> ${message.to || ""}`,
      subject: message.subject || (message.channel ? `#${message.channel}` : ""),
      preview: message.preview || message.body || "",
    }));
}

function compactPacket({ from, targetAgentId, sourceSession, successorId, messages, instructions }) {
  const messageBlock = messages.length
    ? messages.map((message, index) =>
        `${index + 1}. [${message.timestamp || "unknown time"}] ${message.route}\nSubject: ${message.subject || "(none)"}\n${message.preview || ""}`
      ).join("\n\n")
    : "No recent message context selected.";
  return `Handoff compact from previous managed session
Requested by: ${from}
Source agent: ${targetAgentId}
Source session: ${sourceSession.id || ""}
Handoff agent: ${successorId}
Runtime: ${sourceSession.runtime || ""}
Environment: ${sourceSession.environmentId || ""}
Workspace: ${sourceSession.workspace || ""}

Operator instructions:
${instructions || "Continue the same work unless the manager gives a narrower phase brief."}

Recent message context:
${messageBlock}

Current state:

Open tasks:

Next action:`;
}

function internalCompactUnsupportedText(sourceSession = {}) {
  const runtime = normalizeRuntime(sourceSession.runtime || "generic");
  const sessionId = sourceSession.id || "unknown";
  const handle = sourceSession.sessionHandle || sourceSession.session_handle || "";
  const detailByRuntime = {
    "claude-code":
      "Claude Code exposes interactive `/compact`, but aify-comms does not currently have a safe headless managed-run API for triggering that native operation.",
    codex:
      "Codex app-server/CLI currently exposes resume, turn, interrupt, and steer controls, but no native compact/context-reset API.",
    hermes:
      "Hermes support is PTY-backed. Use Hermes's own interactive compression/session tools in the terminal; aify-comms does not have a verified native compact adapter yet.",
    opencode:
      "OpenCode support has no verified native compact adapter yet.",
    pi:
      "Oh My Pi support has no verified native compact adapter yet.",
  };
  const detail = detailByRuntime[runtime] || `Runtime "${runtime}" has no verified native compact adapter.`;
  return [
    `Internal/native compaction is not supported for session "${sessionId}" (${runtime}${handle ? `, handle ${handle}` : ""}).`,
    detail,
    'Use `comms_compact(mode="handoff", ...)` to create a fresh managed backing from an editable handoff packet. Handoff defaults to the same agent ID unless you pass `newAgentId`.',
  ].join("\n");
}

// Registers the compaction tool. A function rather than a module-scope side effect, so a fake server can
// capture the registration and a test can call the handler without an MCP transport. `z` is the caller's zod
// — see the other tool groups for why it is not imported here.
//
// The body below is the original server.js text, indented one level. Nothing else changed.
export function registerCompactTool(server, z) {
  server.tool(
    "comms_compact",
    // C1. Every agent re-reads this on every turn, so the test applied to each sentence was whether it
    // changes what the CALLER does. Three go, and none of them was information:
    //   - "Compact a managed agent/session." restated the tool's own name.
    //   - both `mode="..."` sentences said what the `mode` FIELD's own description already says, and
    //     the "defaults to the same agent ID" half is what `newAgentId` says. A caller filling a
    //     parameter reads the field; saying it twice is one meaning in two places.
    //   - four fields each ended "Defaults to the source session X", so that pattern is stated ONCE
    //     here instead of four times below.
    // What stays is what four existing contracts pin -- `/DESTRUCTIVE/`, `/DESTRUCTIVE TO CONTEXT/`,
    // `/record open decisions somewhere durable FIRST/` and `/durable|write/` -- which is a useful
    // check on the rule: the sentences that change a caller's action are the ones reviewers already
    // insisted on.
    "DESTRUCTIVE TO CONTEXT — the target loses its live working memory and continues from a summary. " +
      "Use when a managed agent is degraded by a long noisy session, not as routine hygiene: whatever it knew but never wrote down is gone. " +
      "Have it record open decisions somewhere durable FIRST. " +
      "environmentId, runtime and workspace each default to the source session's.",
    {
      from: z.string().describe("Manager/coordinator agent requesting the compact"),
      targetAgentId: z.string().describe("Existing managed agent to compact/continue from"),
      mode: z.enum(["handoff", "internal"]).optional().describe("Compaction mode. handoff is the reliable cross-runtime path; internal requests native in-place compaction and may be unsupported."),
      newAgentId: z.string().optional().describe("Agent ID for handoff mode. Defaults to the same target agent ID. Pass a different ID only when you intentionally want a separate continuation identity."),
      role: z.string().optional().describe("Handoff role. Defaults to the target's role, else coder."),
      environmentId: z.string().optional().describe("Target environment."),
      runtime: z.string().optional().describe("Target runtime."),
      workspace: z.string().optional().describe("Target workspace."),
      instructions: z.string().optional().describe("Phase brief or compaction instructions for the fresh backing."),
      recentMessages: z.number().int().min(0).max(80).optional().describe("Recent comms messages to include in the handoff packet. Default 24."),
      priority: z.enum(["normal", "high", "urgent"]).optional().describe("Priority for the handoff initial brief"),
    },
    async ({ from, targetAgentId, mode, newAgentId, role, environmentId, runtime, workspace, instructions, recentMessages, priority }) => {
      if (!IS_REMOTE) {
        return { content: [{ type: "text", text: "Managed compaction requires remote server mode. Start aify-comms against the dashboard service first." }], isError: true };
      }
      try {
        validateName(from, "from agent ID");
        validateName(targetAgentId, "target agent ID");
      } catch (e) {
        return { content: [{ type: "text", text: e.message }], isError: true };
      }

      const agents = (await httpCall("GET", "/agents")).agents || {};
      const targetInfo = agents[targetAgentId] || {};
      const selectedMode = mode || "handoff";
      const successorId = newAgentId || targetAgentId;
      try { validateName(successorId, "handoff agent ID"); } catch (e) { return { content: [{ type: "text", text: e.message }], isError: true }; }

      const sessionsRes = await httpCall("GET", `/sessions?agentId=${encodeURIComponent(targetAgentId)}&limit=100`);
      const sourceSession = pickCompactSession(sessionsRes.sessions || []);
      if (!sourceSession) {
        return {
          content: [{
            type: "text",
            text: `No managed session record found for "${targetAgentId}". Compact needs a dashboard-managed backing session. Use comms_spawn first or adopt the identity into an environment from the dashboard.`,
          }],
          isError: true,
        };
      }

      if (selectedMode === "internal") {
        return {
          content: [{ type: "text", text: internalCompactUnsupportedText(sourceSession) }],
          isError: true,
        };
      }

      const count = Math.max(0, Math.min(80, Number(recentMessages ?? 24)));
      const recentLimit = Math.min(250, Math.max(80, count * 4 || 80));
      const recentRes = await httpCall("GET", `/messages/recent?limit=${recentLimit}`);
      const contextMessages = messageContextForCompact(recentRes.messages || [], targetAgentId, count);
      const packet = compactPacket({
        from,
        targetAgentId,
        sourceSession,
        successorId,
        messages: contextMessages,
        instructions,
      });

      const resolvedRuntime = normalizeRuntime(runtime || sourceSession.runtime || targetInfo.runtime || "generic");
      const r = await httpCall("POST", "/spawn-requests", {
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
          contextMessageCount: contextMessages.length,
          sameAgentId: successorId === targetAgentId,
        },
      });
      const req = r.spawnRequest || {};
      const identityText = successorId === targetAgentId
        ? `same agent ID "${successorId}"`
        : `successor "${successorId}"`;
      return {
        content: [{
          type: "text",
          text:
            `Queued handoff compaction for ${identityText} from "${targetAgentId}" with ${contextMessages.length} recent message(s). ` +
            `Spawn request: ${req.id || "unknown"} [${req.status || "queued"}]. This creates a fresh managed backing; the old native session is not reused.`,
        }],
      };
    }
  );
}
