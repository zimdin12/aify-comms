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

import fs from "fs";
import path from "path";

import { IS_REMOTE, httpCall } from "./aify-service-endpoint.mjs";
import { MESSAGES_DIR, markAsRead, readAgents, readInbox, writeAgents } from "./local-store.mjs";
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

      if (IS_REMOTE) {
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

      const registry = readAgents();
      if (registry.agents[agentId]) {
        registry.agents[agentId].lastSeen = new Date().toISOString();
        writeAgents(registry);
      }

      let messages = readInbox(agentId, messageId ? "all" : msgFilter);
      if (fromAgent) messages = messages.filter((m) => m.from === fromAgent);
      if (fromRole) {
        messages = messages.filter((m) => {
          const s = registry.agents[m.from];
          return s && s.role === fromRole;
        });
      }
      if (type) messages = messages.filter((m) => m.type === type);
      if (messageId) messages = messages.filter((m) => m.id === messageId);

      const total = messages.length;
      if (total === 0) {
        return { content: [{ type: "text", text: messageId ? `Message ${messageId} not found in inbox.` : "Inbox empty." }] };
      }

      const shown = messages.slice(0, messageId ? 1 : maxN);
      if (!peek) markAsRead(agentId, shown);

      const formatted = shown.map((m) => (inboxMode === "headers" ? formatInboxHeaders(m, registry) : formatInboxMessage(m, registry)));
      const truncNote = !messageId && total > maxN ? `\n\n(Showing ${maxN} of ${total}. Use limit param for more.)` : "";
      return {
        content: [{ type: "text", text: `${SAFETY_HEADER}\n\n${total} message(s):\n\n${formatted.join("\n\n")}${truncNote}` }],
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
      if (IS_REMOTE) {
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
      // Local mode. The id is matched EXACTLY against the stored message and the sender must be the
      // caller: this matched a two-segment filename prefix in ANY inbox until 0.7.0, so an agent could
      // delete another agent's message (v0.7 scan B18). A message sent to several recipients has one
      // copy per inbox, and unsending takes back every copy.
      const inbox = path.join(MESSAGES_DIR, "inbox");
      let deleted = 0;
      let notYours = 0;
      try {
        for (const agentDir of fs.readdirSync(inbox)) {
          const dir = path.join(inbox, agentDir);
          if (!fs.statSync(dir).isDirectory()) continue;
          for (const f of fs.readdirSync(dir)) {
            if (!f.endsWith(".json")) continue;
            let stored;
            try { stored = JSON.parse(fs.readFileSync(path.join(dir, f), "utf-8")); } catch { continue; }
            if (stored?.id !== messageId) continue;
            if (stored.from !== from) { notYours += 1; continue; }
            fs.unlinkSync(path.join(dir, f));
            deleted += 1;
          }
        }
      } catch { /* best effort */ }
      if (deleted) return { content: [{ type: "text", text: `Deleted message ${messageId}.` }] };
      if (notYours) {
        return { content: [{ type: "text", text: `Message ${messageId} was not sent by ${from}; only its sender may unsend it.` }], isError: true };
      }
      return { content: [{ type: "text", text: `Message ${messageId} not found.` }], isError: true };
    }
  );
}
