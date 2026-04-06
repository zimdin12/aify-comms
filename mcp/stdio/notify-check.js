#!/usr/bin/env node
/**
 * aify-claude inbox notification checker + heartbeat.
 * Called by Claude Code PostToolUse hook.
 *
 * 1. Sends heartbeat to server (signals agent is alive/working)
 * 2. Checks for unread messages
 * 3. Prints notification if unread messages exist
 */

import fs from "fs";
import path from "path";
import { loadSettingsEnv } from "./load-env.js";

loadSettingsEnv();

const SERVER_URL = process.argv[2] || process.env.CLAUDE_MCP_SERVER_URL || process.env.AIFY_SERVER_URL || "";
const API_KEY = process.env.CLAUDE_MCP_API_KEY || process.env.AIFY_API_KEY || "";
const tmpDir = process.env.TEMP || process.env.TMP || "/tmp";

if (!SERVER_URL) process.exit(0);

// If server was unreachable recently, skip entirely (check every 60s)
const DOWN_FILE = path.join(tmpDir, "aify-server-down.ts");
try {
  const lastDown = parseInt(fs.readFileSync(DOWN_FILE, "utf-8"), 10);
  if (Date.now() - lastDown < 60_000) process.exit(0);
} catch { /* no file = never failed */ }

// Find agent ID: check session-specific temp file first (by parent PID), then cwd
let agentId = "";
const SESSION_FILE = path.join(tmpDir, `aify-agent-${process.ppid || ""}`);
const CWD_FILE = path.join(process.cwd(), ".aify-agent");

if (fs.existsSync(SESSION_FILE)) {
  agentId = fs.readFileSync(SESSION_FILE, "utf-8").trim();
} else if (fs.existsSync(CWD_FILE)) {
  agentId = fs.readFileSync(CWD_FILE, "utf-8").trim();
}
if (!agentId) process.exit(0);

// Rate limit: only check every 30 seconds
const RATE_FILE = path.join(process.env.TEMP || "/tmp", `aify-notify-${agentId}.ts`);
try {
  const lastCheck = parseInt(fs.readFileSync(RATE_FILE, "utf-8"), 10);
  if (Date.now() - lastCheck < 30_000) process.exit(0);
} catch { /* first check */ }
fs.writeFileSync(RATE_FILE, String(Date.now()));

const headers = { "Accept": "application/json" };
if (API_KEY) headers["X-API-Key"] = API_KEY;

try {
  // Heartbeat — signals agent is alive (sets status to "working")
  fetch(`${SERVER_URL}/api/v1/agents/${agentId}/heartbeat`, {
    method: "POST", headers, signal: AbortSignal.timeout(2000),
  }).catch(() => {});

  // Check inbox
  const url = `${SERVER_URL}/api/v1/messages/inbox/${agentId}?filter=unread&limit=3&peek=true`;
  const resp = await fetch(url, { headers, signal: AbortSignal.timeout(3000) });
  if (!resp.ok) process.exit(0);
  // Server is up — clear any previous down marker
  try { fs.unlinkSync(DOWN_FILE); } catch {}
  const data = await resp.json();

  if (data.total > 0) {
    const msgs = data.messages || [];
    const hasUrgent = msgs.some(m => m.priority === "urgent" || m.priority === "high");
    const tag = hasUrgent ? " ⚠ URGENT" : "";
    const previews = msgs.map(m => {
      const p = (m.priority && m.priority !== "normal") ? ` [${m.priority.toUpperCase()}]` : "";
      return `  - From ${m.from}${p}: "${m.subject}"`;
    }).join("\n");
    const more = data.total > 3 ? `\n  ...and ${data.total - 3} more` : "";
    console.log(`[aify-claude]${tag} ${data.total} unread message(s):\n${previews}${more}\nUse cc_inbox to read them.`);
  }
} catch {
  // Server unreachable — cache the failure so we skip quickly next time
  try { fs.writeFileSync(DOWN_FILE, String(Date.now())); } catch {}
  process.exit(0);
}
