// An agent's own inbox: reading it, and retracting from it.
//
// Two MCP tools — `comms_inbox`, `comms_unsend`. v0.5.4 layer 2 of the server.js decomposition, the
// third tool group to move.
//
// THE SUBJECT IS DELIBERATELY NARROW. `comms_search` sits between two of these in server.js and did NOT
// come along: it searches messages AND shared artifacts across the corpus, which is a different subject
// from the caller's own mailbox, and folding it in for the line count would have made this module
// "message-ish things" rather than an inbox. Same reason `comms_agent_info` stayed — it reports on
// SOMEONE ELSE.
//
// WHAT THESE ACTUALLY GUARD. Every message here is written by another agent, so every rendering path
// prepends `SAFETY_HEADER` — the line that tells a reading model the content is data, not instructions.
// That banner has one owner (`tool-response-format.mjs`) precisely so these tools cannot come to
// disagree about it.
//
// Nothing here was reachable from a test before the move: server.js is the bin entry point and nothing
// imports it.
//
// The `// 4. // 5c.` banners are the original text; their numbers refer to server.js's tool
// ordering, which is no longer one list. Kept rather than renumbered — inventing new ones here would
// only make two files disagree about a navigation aid.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.


import { httpCall } from "./aify-service-endpoint.mjs";
import { validateName } from "./safe-name.mjs";
import { SAFETY_HEADER, formatInboxHeaders, formatInboxMessage } from "./tool-response-format.mjs";

// Registers the two inbox tools on an MCP server.
//
// A function, not a module-scope side effect: registration at import time would fire on any import,
// including a test's, and a fake server is how these handlers become callable without an MCP transport.
// `z` is the caller's zod — server.js loads it below its `AIFY_BRIDGE_DISABLED` early-exit so an RPC
// child never pays for it, and a static import here would be hoisted above that guard.
//
// The two bodies below are the original server.js text, indented one level to sit inside this
// function. Nothing else about them changed.
export function registerInboxTools(server, z) {
  // ═══════════════════════════════════════════════════════════════════════════════
  // 4. comms_inbox -- Check inbox, unread only by default
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_inbox",
    "Read your inbox: UNREAD messages by default, 20 at a time. Viewing MARKS MESSAGES READ, mode=headers included; pass peek=true to leave them unread. messageId fetches one message.",
    {
      agentId: z.string().describe("Your agent ID"),
      filter: z.enum(["unread", "read", "all"]).optional().describe("Which messages (default: unread)"),
      fromAgent: z.string().optional().describe("Filter by sender agent ID"),
      fromRole: z.string().optional().describe("Filter by sender role"),
      type: z.string().optional().describe("Filter by message type"),
      mode: z.enum(["full", "headers"]).optional().describe("full (default) or headers; both mark read"),
      messageId: z.string().optional().describe("Fetch one message by ID, read or unread."),
      limit: z.number().optional().describe("Max messages (default: 20)"),
      peek: z.boolean().optional().describe("Leave the returned messages UNREAD."),
    },
    async ({ agentId, filter, fromAgent, fromRole, type, mode, messageId, limit, peek }) => {
      try { validateName(agentId, "agent ID"); } catch (e) { return { content: [{ type: "text", text: e.message }], isError: true }; }

      const maxN = limit || 20;
      const msgFilter = filter || "unread";
      const inboxMode = mode || "full";

      const params = new URLSearchParams({ filter: msgFilter, limit: String(maxN), mode: inboxMode });
      if (fromAgent) params.set("fromAgent", fromAgent);
      if (fromRole) params.set("fromRole", fromRole);
      if (type) params.set("type", type);
      if (messageId) params.set("messageId", messageId);
      // SET ONLY WHEN TRUE. The route gates on `bool(peek)` over a STRING, so any non-empty
      // value counts and `peek=false` would read as peek. Omitting is the only way to say no.
      if (peek) params.set("peek", "1");
      const r = await httpCall("GET", `/messages/inbox/${agentId}?${params}`);
      if (!r.messages.length) {
        return { content: [{ type: "text", text: messageId ? `Message ${messageId} not found in inbox.` : "Inbox empty." }] };
      }
      const formatter = inboxMode === "headers" ? formatInboxHeaders : formatInboxMessage;
      const lines = r.messages.map((m) => formatter(m, null));
      const trunc = r.total > r.showing ? `\n\n(Showing ${r.showing} of ${r.total}, newest first. Use limit param for more.)` : "";
      return {
        content: [{ type: "text", text: `${SAFETY_HEADER}\n\n${r.total} message(s):\n\n${lines.join("\n\n")}${trunc}` }],
      };
    }
  );

  // ═══════════════════════════════════════════════════════════════════════════════
  // 5c. comms_unsend -- Delete a message by ID
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_unsend",
    "Take back a message YOU sent, by its ID.",
    {
      messageId: z.string().describe("The message ID to delete"),
      from: z.string().describe("Your agent ID; must be the sender"),
    },
    async ({ messageId, from }) => {
      try {
        // H4 (2026-08-18): the service now REQUIRES an actor and refuses a mismatch. It used to
        // delete any message by id with no ownership check at all, which this tool exposed to
        // every agent.
        const r = await httpCall(
          "DELETE",
          `/messages/${encodeURIComponent(messageId)}?requestedBy=${encodeURIComponent(from || "")}`,
        );
        return { content: [{ type: "text", text: `Deleted message ${messageId}.` }] };
      } catch (e) {
        return { content: [{ type: "text", text: `Failed to delete: ${e.message}` }], isError: true };
      }
    }
  );
}
