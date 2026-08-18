// Searching what an agent can see: its own messages, and the shared artifacts.
//
// One MCP tool, `comms_search`. v0.5.4 layer 2 of the server.js decomposition.
//
// A ONE-TOOL MODULE, DELIBERATELY. This tool sits physically between `comms_inbox` and `comms_listen` in
// server.js and was excluded from the inbox group when that moved: an inbox is the caller's own mailbox,
// and this searches the whole corpus — messages the agent SENT as well as received, plus every shared
// artifact. Folding it in for the adjacency would have made that module "message-ish things". Its subject
// is search, and search is what it is alone with.
//
// WHAT THIS TOOL EXISTS TO PREVENT, and it is not "finding things". An empty result was being read as
// "no such message exists" when messages had not been searched AT ALL — omitting `agentId` searches
// shared artifacts only. So an empty answer licensed work that had already been ruled out: the tool
// failed OPEN. Every response therefore reports what it actually searched, and warns about what it did
// not. The tests below assert that scope note is present in BOTH transports' shapes, because a silent
// scope is the whole defect.
//
// The remote branch also renders NO read/unread marker, and that absence is deliberate. The search
// endpoint does not return read state, so `x.read` was always undefined and every hit rendered as "NEW"
// — including messages the agent had sent itself, where unread is not a meaningful property. A marker
// that is always on carries no information and quietly misleads.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import fs from "fs";
import path from "path";

import { IS_REMOTE, httpCall } from "./aify-service-endpoint.mjs";
import { SHARED_DIR, readInbox } from "./local-store.mjs";
import { quoteUntrustedSubject } from "./quote-subject.mjs";

// Registers the search tool on an MCP server. A function rather than a module-scope side effect, so a
// fake server can capture the registration and a test can call the handler without an MCP transport.
// `z` is the caller's zod — see the other tool groups for why it is not imported here.
//
// The body below is the original server.js text, indented one level. Nothing else about it changed.
export function registerSearchTool(server, z) {
  // ═══════════════════════════════════════════════════════════════════════════════
  // 5. comms_search -- Search inbox messages and shared artifacts by keyword
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_search",
    "Search an agent's messages (sent AND received) and shared artifacts by keyword. " +
      "PASS agentId, or messages are NOT searched at all and you only get shared files — an empty " +
      "result would then say nothing about whether the message exists. The response always reports " +
      "what it actually searched; read it before treating an empty result as absence.",
    {
      agentId: z.string().optional().describe(
        "Whose record to search — matches messages this agent SENT or RECEIVED. " +
        "OMIT AND MESSAGES ARE NOT SEARCHED (shared artifacts only)."),
      query: z.string().describe("Search term (case-insensitive, matches subject + body)"),
      scope: z.enum(["inbox", "shared", "all"]).optional().describe("Where to search (default: all)"),
      limit: z.number().optional().describe("Max results (default: 10)"),
    },
    async ({ agentId, query, scope, limit }) => {
      const maxN = limit || 10;
      const searchScope = scope || "all";

      if (IS_REMOTE) {
        const params = new URLSearchParams({ query, scope: searchScope, limit: String(maxN) });
        if (agentId) params.set("agentId", agentId);
        const r = await httpCall("GET", `/messages/search?${params}`);
        // ALWAYS say what was searched. "No results" alone was being read as "no such message
        // exists" when messages had not been searched at all (no agentId ⇒ shared artifacts only),
        // which let an empty result license work that had already been ruled — it failed OPEN.
        const scopeNote = Array.isArray(r.searched) && r.searched.length
          ? `searched: ${r.searched.join(" + ")}`
          : "searched: nothing";
        const warn = Array.isArray(r.skipped) && r.skipped.length
          ? `\n⚠ NOT searched: ${r.skipped.join("; ")}. An empty result here is NOT evidence that no such message exists.`
          : "";
        if (!r.results.length) {
          return { content: [{ type: "text", text: `No results for "${query}" (${scopeNote}).${warn}` }] };
        }
        const lines = r.results.map((x) =>
          x.type === "message"
            // No NEW/read marker. The search endpoint does not return read state, so `x.read` was
            // always undefined and EVERY hit rendered as "NEW" — including messages the agent sent
            // itself, where unread is not a meaningful property at all. Reviewer's catch. A marker
            // that is always on carries no information and quietly misleads.
            // QUOTED: a search result is somebody else's subject rendered into the reader's context
            // with the addressing stripped off — the exact shape that made an agent restart itself.
            // The service side has quoted its echoes since 2026-08-11; this transport never did.
            ? `[MSG] ${x.id} | from: ${x.from}${x.to ? ` → ${x.to}` : ""} | ${quoteUntrustedSubject(x.subject, 120)}\n  ${x.preview}`
            : `[FILE] ${x.name} | from: ${x.from} | ${x.description}`
        );
        return { content: [{ type: "text", text: `${lines.join("\n\n")}\n\n(${scopeNote})${warn}` }] };
      }

      const q = query.toLowerCase();
      const results = [];

      // Search inbox messages
      if (agentId && (searchScope === "inbox" || searchScope === "all")) {
        for (const m of readInbox(agentId, "all")) {
          const haystack = `${m.subject || ""} ${m.body || ""} ${m.from || ""}`.toLowerCase();
          if (haystack.includes(q)) {
            results.push({
              type: "message",
              read: m._read,
              id: m.id,
              from: m.from,
              subject: m.subject,
              time: new Date(m.timestamp).toISOString(),
              preview: (m.body || "").slice(0, 150),
            });
          }
        }
      }

      // Search shared artifacts
      if (searchScope === "shared" || searchScope === "all") {
        try {
          const files = fs.readdirSync(SHARED_DIR).filter((f) => !f.endsWith(".meta.json"));
          for (const f of files) {
            const filePath = path.join(SHARED_DIR, f);
            let meta = {};
            try { meta = JSON.parse(fs.readFileSync(filePath + ".meta.json", "utf-8")); } catch { /* no meta */ }

            const haystack = `${f} ${meta.description || ""} ${meta.from || ""}`.toLowerCase();
            let contentMatch = false;
            try {
              const stat = fs.statSync(filePath);
              if (stat.size < 1_000_000) {
                if (fs.readFileSync(filePath, "utf-8").toLowerCase().includes(q)) contentMatch = true;
              }
            } catch { /* binary or unreadable */ }

            if (haystack.includes(q) || contentMatch) {
              results.push({
                type: "artifact",
                name: f,
                from: meta.from || "unknown",
                description: meta.description || "",
                size: meta.size || 0,
              });
            }
          }
        } catch { /* no shared dir */ }
      }

      if (!results.length) return { content: [{ type: "text", text: `No results for "${query}".` }] };

      const shown = results.slice(0, maxN);
      const lines = shown.map((r) =>
        r.type === "message"
          ? `[MSG${r.read ? "" : " NEW"}] ${r.id} | from: ${r.from} | ${r.subject}\n  ${r.preview}`
          : `[FILE] ${r.name} | from: ${r.from} | ${r.description}`
      );
      const truncNote = results.length > maxN ? `\n(${results.length} total, showing ${maxN})` : "";
      return { content: [{ type: "text", text: lines.join("\n\n") + truncNote }] };
    }
  );
}
