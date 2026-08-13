// Opening the dashboard, and building one when there is no server to open.
//
// One MCP tool, `comms_dashboard`. v0.5.4 layer 2 of the server.js decomposition.
//
// TWO IMPLEMENTATIONS WITH LITTLE IN COMMON, which is why it is its own module rather than part of a
// reporting group. In remote mode it opens the service's dashboard URL. In local mode there is no service,
// so it GENERATES an HTML file from the filesystem store and opens that — a self-contained view of agents,
// inboxes and shared artifacts assembled on the spot. The second half is most of the code and shares
// nothing with the rest of the bridge except the store it reads.
//
// FLAGGED, NOT CHANGED — the launch is a shell spawn. Remote mode builds a URL containing the configured
// API key and passes it to the platform opener with `shell: true`. Both inputs are operator-configured
// (`AIFY_SERVER_URL` and the API key from the environment), so nothing an agent or a message can influence
// reaches that command line — but a key containing shell metacharacters would be interpreted rather than
// passed, and `shell: false` with an argv array would remove the question entirely. Behavioural change, so
// it stays as it is and is recorded here instead.
//
// The `// 16.` banner is the original text; its number refers to server.js's tool ordering, which no longer
// exists as one list.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { spawn } from "child_process";
import fs from "fs";
import path from "path";

import { API_KEY, IS_REMOTE, SERVER_URL } from "./aify-service-endpoint.mjs";
import { INBOX_DIR, MESSAGES_DIR, SHARED_DIR, readAgents } from "./local-store.mjs";

// Registers the dashboard tool on an MCP server. A function rather than a module-scope side effect, so a
// fake server can capture the registration and a test can call the handler without an MCP transport.
// `z` is the caller's zod — see the other tool groups for why it is not imported here.
//
// The body below is the original server.js text, indented one level. Nothing else changed.
export function registerDashboardTool(server, z) {
  // ═══════════════════════════════════════════════════════════════════════════════
  // 16. comms_dashboard -- Open dashboard in browser
  // ═══════════════════════════════════════════════════════════════════════════════

  server.tool(
    "comms_dashboard",
    "Open the dashboard in a browser. Remote mode opens the server dashboard URL. " +
      "Local mode generates a minimal HTML file with current state.",
    {
      open: z.boolean().optional().describe("Auto-open in browser (default: true)"),
    },
    async ({ open }) => {
      const openCmd =
        process.platform === "win32" ? "start" : process.platform === "darwin" ? "open" : "xdg-open";

      // Remote mode: open the server's dashboard directly
      if (IS_REMOTE) {
        const dashUrl = `${SERVER_URL}/api/v1/dashboard${API_KEY ? "?api_key=" + API_KEY : ""}`;
        if (open !== false) {
          spawn(openCmd, [dashUrl], { shell: true, detached: true, stdio: "ignore" }).unref();
        }
        return { content: [{ type: "text", text: `Dashboard: ${dashUrl}${open !== false ? "\nOpened in browser." : ""}` }] };
      }

      // Local mode: generate a minimal summary HTML file
      const registry = readAgents();
      const agents = Object.entries(registry.agents);

      // Collect messages
      const allMessages = [];
      try {
        for (const dir of fs.readdirSync(INBOX_DIR)) {
          const dirPath = path.join(INBOX_DIR, dir);
          try {
            for (const f of fs.readdirSync(dirPath).filter((f) => f.endsWith(".json")).sort()) {
              try {
                const msg = JSON.parse(fs.readFileSync(path.join(dirPath, f), "utf-8"));
                msg._to = dir;
                msg._read = f.endsWith(".read.json");
                allMessages.push(msg);
              } catch { /* skip corrupt */ }
            }
          } catch { /* skip */ }
        }
      } catch { /* no inbox dir */ }
      allMessages.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));

      // Collect shared files
      const sharedFiles = [];
      try {
        for (const f of fs.readdirSync(SHARED_DIR).filter((f) => !f.endsWith(".meta.json"))) {
          let meta = {};
          try { meta = JSON.parse(fs.readFileSync(path.join(SHARED_DIR, f + ".meta.json"), "utf-8")); } catch { /* no meta */ }
          const stat = fs.statSync(path.join(SHARED_DIR, f));
          sharedFiles.push({ name: f, ...meta, size: stat.size, modified: stat.mtimeMs });
        }
      } catch { /* no shared dir */ }

      const esc = (s) => String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const now = new Date().toLocaleString();

      const agentRows = agents
        .map(([id, info]) => {
          const unread = allMessages.filter((m) => m._to === id && !m._read).length;
          return `<tr><td>${esc(id)}</td><td>${esc(info.role)}</td><td>${esc(info.name)}</td><td>${unread}</td><td>${info.lastSeen || "?"}</td></tr>`;
        })
        .join("");

      const msgRows = allMessages
        .slice(0, 50)
        .map((m) => {
          const time = m.timestamp ? new Date(m.timestamp).toLocaleString() : "?";
          const tag = m._read ? "" : " *";
          return `<tr><td>${time}${tag}</td><td>${esc(m.from)}</td><td>${esc(m._to)}</td><td>${esc(m.type)}</td><td>${esc(m.subject)}</td></tr>`;
        })
        .join("");

      const fileRows = sharedFiles
        .map((f) => {
          const size = f.size > 1024 ? `${(f.size / 1024).toFixed(1)}KB` : `${f.size}B`;
          return `<tr><td>${esc(f.name)}</td><td>${esc(f.from || "?")}</td><td>${size}</td><td>${esc(f.description || "")}</td></tr>`;
        })
        .join("");

      const html = `<!DOCTYPE html>
  <html><head><meta charset="UTF-8"><title>MCP Dashboard</title>
  <style>body{font-family:system-ui;background:#0d1117;color:#c9d1d9;margin:20px}
  h1{color:#58a6ff}h2{color:#58a6ff;border-bottom:1px solid #30363d;padding-bottom:6px}
  table{border-collapse:collapse;width:100%;margin-bottom:24px;background:#161b22}
  th,td{text-align:left;padding:8px 12px;border:1px solid #21262d;font-size:.9em}
  th{background:#21262d;color:#8b949e}tr:hover{background:#1c2128}
  .stats{display:flex;gap:12px;margin-bottom:20px}
  .stat{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 18px}
  .stat b{font-size:1.6em;color:#58a6ff;display:block}</style></head><body>
  <h1>MCP Dashboard (local)</h1><p style="color:#8b949e">Generated: ${now}</p>
  <div class="stats">
  <div class="stat"><b>${agents.length}</b>Agents</div>
  <div class="stat"><b>${allMessages.filter((m) => !m._read).length}</b>Unread</div>
  <div class="stat"><b>${allMessages.length}</b>Messages</div>
  <div class="stat"><b>${sharedFiles.length}</b>Files</div></div>
  <h2>Agents</h2>${agents.length ? `<table><tr><th>ID</th><th>Role</th><th>Name</th><th>Unread</th><th>Last Seen</th></tr>${agentRows}</table>` : "<p>No agents.</p>"}
  <h2>Messages (last 50)</h2>${allMessages.length ? `<table><tr><th>Time</th><th>From</th><th>To</th><th>Type</th><th>Subject</th></tr>${msgRows}</table>` : "<p>No messages.</p>"}
  <h2>Shared Files</h2>${sharedFiles.length ? `<table><tr><th>Name</th><th>From</th><th>Size</th><th>Description</th></tr>${fileRows}</table>` : "<p>No files.</p>"}
  <p style="color:#484f58;text-align:center;margin-top:30px">Snapshot. Run comms_dashboard again to refresh.</p>
  </body></html>`;

      const dashPath = path.join(MESSAGES_DIR, "dashboard.html");
      fs.writeFileSync(dashPath, html);

      if (open !== false) {
        spawn(openCmd, [dashPath], { shell: true, detached: true, stdio: "ignore" }).unref();
      }

      return {
        content: [{
          type: "text",
          text: `Dashboard: ${dashPath.replace(/\\/g, "/")}\n` +
            `${agents.length} agents, ${allMessages.length} messages, ${sharedFiles.length} files.` +
            (open !== false ? "\nOpened in browser." : ""),
        }],
      };
    }
  );
}
