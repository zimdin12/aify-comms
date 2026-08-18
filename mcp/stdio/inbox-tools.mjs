// An agent's own inbox: reading it, waiting on it, and retracting from it.
//
// Three MCP tools — `comms_inbox`, `comms_listen`, `comms_unsend`. v0.5.4 layer 2 of the server.js
// decomposition, the third tool group to move.
//
// THE SUBJECT IS DELIBERATELY NARROW. `comms_search` sits between two of these in server.js and did NOT
// come along: it searches messages AND shared artifacts across the corpus, which is a different subject
// from the caller's own mailbox, and folding it in for the line count would have made this module
// "message-ish things" rather than an inbox. Same reason `comms_agent_info` stayed — it reports on
// SOMEONE ELSE.
//
// WHAT THESE ACTUALLY GUARD. Every message here is written by another agent, so every rendering path
// prepends `SAFETY_HEADER` — the line that tells a reading model the content is data, not instructions.
// That banner has one owner (`tool-response-format.mjs`) precisely so two of these three cannot come to
// disagree about it. `comms_listen` is a deprecated long-poll kept for compatibility and behaves
// differently under managed dispatch, which is why it reads `IS_MANAGED_DISPATCH`.
//
// Nothing here was reachable from a test before the move: server.js is the bin entry point and nothing
// imports it.
//
// The `// 4. // 5c. // 5d.` banners are the original text; their numbers refer to server.js's tool
// ordering, which is no longer one list. Kept rather than renumbered — inventing new ones here would
// only make two files disagree about a navigation aid.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import fs from "fs";
import path from "path";

import { API_KEY, IS_REMOTE, SERVER_URL, httpCall } from "./aify-service-endpoint.mjs";
import { IS_MANAGED_DISPATCH } from "./launch-identity.mjs";
import { MESSAGES_DIR, markAsRead, readAgents, readInbox, writeAgents } from "./local-store.mjs";
import { validateName } from "./safe-name.mjs";
import { SAFETY_HEADER, formatInboxHeaders, formatInboxMessage } from "./tool-response-format.mjs";

// Registers the three inbox tools on an MCP server.
//
// A function, not a module-scope side effect: registration at import time would fire on any import,
// including a test's, and a fake server is how these handlers become callable without an MCP transport.
// `z` is the caller's zod — server.js loads it below its `AIFY_BRIDGE_DISABLED` early-exit so an RPC
// child never pays for it, and a static import here would be hoisted above that guard.
//
// The three bodies below are the original server.js text, indented one level to sit inside this
// function. Nothing else about them changed.
export function registerInboxTools(server, z) {
  // ═══════════════════════════════════════════════════════════════════════════════
  // 4. comms_inbox -- Check inbox, unread only by default
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_inbox",
    "Check your inbox. Returns only UNREAD messages by default (limit 20). " +
      "Messages are automatically marked as read after viewing. Use mode=headers for preview-only triage or messageId to fetch one message by ID.",
    {
      agentId: z.string().describe("Your agent ID"),
      filter: z.enum(["unread", "read", "all"]).optional().describe("Which messages (default: unread)"),
      fromAgent: z.string().optional().describe("Filter by sender agent ID"),
      fromRole: z.string().optional().describe("Filter by sender role"),
      type: z.string().optional().describe("Filter by message type"),
      mode: z.enum(["full", "headers"]).optional().describe("Return full bodies or header/preview only (default: full)"),
      messageId: z.string().optional().describe("Fetch one specific inbox message by ID. Overrides the unread/read filter."),
      limit: z.number().optional().describe("Max messages (default: 20)"),
    },
    async ({ agentId, filter, fromAgent, fromRole, type, mode, messageId, limit }) => {
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
        const r = await httpCall("GET", `/messages/inbox/${agentId}?${params}`);
        if (!r.messages.length) {
          return { content: [{ type: "text", text: messageId ? `Message ${messageId} not found in inbox.` : "Inbox empty." }] };
        }
        const formatter = inboxMode === "headers" ? formatInboxHeaders : formatInboxMessage;
        const lines = r.messages.map((m) => formatter(m, null));
        const trunc = r.total > r.showing ? `\n\n(Showing ${r.showing} of ${r.total})` : "";
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
      markAsRead(agentId, shown);

      const formatted = shown.map((m) => (inboxMode === "headers" ? formatInboxHeaders(m, registry) : formatInboxMessage(m, registry)));
      const truncNote = !messageId && total > maxN ? `\n\n(Showing ${maxN} of ${total}. Use limit param for more.)` : "";
      return {
        content: [{ type: "text", text: `${SAFETY_HEADER}\n\n${total} message(s):\n\n${formatted.join("\n\n")}${truncNote}` }],
      };
    }
  );

  // ═══════════════════════════════════════════════════════════════════════════════
  // 5d. comms_listen -- Deprecated compatibility/debug long-poll
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_listen",
    "Deprecated compatibility/debug long-poll for incoming messages. Blocks until a message arrives or timeout. " +
      "Do not use for normal teamwork or active managed dispatch turns; use bridge wake delivery, comms_inbox, and comms_send instead. " +
      "Returns immediately if you already have unread messages.",
    {
      agentId: z.string().describe("Your agent ID"),
      timeout: z.number().optional().describe("Max seconds to wait (default: 300, max: 600)"),
    },
    async ({ agentId, timeout }) => {
      try { validateName(agentId, "agent ID"); } catch (e) { return { content: [{ type: "text", text: e.message }], isError: true }; }
      if (IS_MANAGED_DISPATCH) {
        return {
          content: [{
            type: "text",
            text:
              "comms_listen is disabled during managed dispatch turns because it can block the active run. " +
              "Use the message already delivered in the prompt, comms_inbox for a quick explicit check, or comms_send to reply.",
          }],
          isError: true,
        };
      }
      const maxWait = Math.min(timeout || 300, 600);

      if (IS_REMOTE) {
        const url = `${SERVER_URL}/api/v1/agents/${agentId}/listen?timeout=${maxWait}`;
        const options = { headers: {}, signal: AbortSignal.timeout((maxWait + 10) * 1000) };
        if (API_KEY) options.headers["X-API-Key"] = API_KEY;
        try {
          const res = await fetch(url, options);
          const r = await res.json();
          if (!r.messages || r.messages.length === 0) {
            return { content: [{ type: "text", text: "No messages received (timeout). comms_listen is deprecated compatibility/debug long-polling; use bridge wake delivery and comms_inbox for normal work." }] };
          }
          const registry = {};
          try { const a = await httpCall("GET", "/agents"); registry.agents = a.agents; } catch {}
          const formatted = r.messages.map((m) => formatInboxMessage(m, registry));
          return {
            content: [{ type: "text", text: `${SAFETY_HEADER}\n\n${r.total} message(s) received:\n\n${formatted.join("\n\n")}` }],
          };
        } catch (e) {
          if (e.name === "TimeoutError" || e.name === "AbortError" || /fetch failed|ECONNREFUSED|ECONNRESET|ETIMEDOUT|socket/i.test(e.message)) {
            return { content: [{ type: "text", text: "No messages received (connection interrupted). comms_listen is deprecated compatibility/debug long-polling; use bridge wake delivery and comms_inbox for normal work." }] };
          }
          return { content: [{ type: "text", text: `Listen error: ${e.message}` }], isError: true };
        }
      }

      // Local mode — poll inbox
      const deadline = Date.now() + maxWait * 1000;
      while (Date.now() < deadline) {
        const messages = readInbox(agentId, "unread");
        if (messages.length > 0) {
          markAsRead(agentId, messages);
          const registry = readAgents();
          if (registry.agents[agentId]) {
            registry.agents[agentId].status = "working";
            registry.agents[agentId].lastSeen = new Date().toISOString();
            writeAgents(registry);
          }
          const formatted = messages.map((m) => formatInboxMessage(m, registry));
          return {
            content: [{ type: "text", text: `${SAFETY_HEADER}\n\n${messages.length} message(s) received:\n\n${formatted.join("\n\n")}` }],
          };
        }
        await new Promise((resolve) => setTimeout(resolve, 2000));
      }
      return { content: [{ type: "text", text: "No messages received (timeout). comms_listen is deprecated compatibility/debug long-polling; use bridge wake delivery and comms_inbox for normal work." }] };
    }
  );

  // ═══════════════════════════════════════════════════════════════════════════════
  // 5c. comms_unsend -- Delete a message by ID
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_unsend",
    "Take back a message YOU sent, by its ID. Only the sender (or the operator) may unsend.",
    {
      messageId: z.string().describe("The message ID to delete"),
      from: z.string().describe("Your agent ID — the sender taking its own message back"),
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
      // Local mode: find and delete the file
      const inbox = path.join(MESSAGES_DIR, "inbox");
      try {
        for (const agentDir of fs.readdirSync(inbox)) {
          const dir = path.join(inbox, agentDir);
          if (!fs.statSync(dir).isDirectory()) continue;
          for (const f of fs.readdirSync(dir)) {
            if (f.includes(messageId.split("-").slice(0, 2).join("-"))) {
              fs.unlinkSync(path.join(dir, f));
              return { content: [{ type: "text", text: `Deleted message ${messageId}.` }] };
            }
          }
        }
      } catch { /* best effort */ }
      return { content: [{ type: "text", text: `Message ${messageId} not found.` }], isError: true };
    }
  );
}
