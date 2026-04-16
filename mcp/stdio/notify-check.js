#!/usr/bin/env node
/**
 * aify-comms inbox notification checker + heartbeat.
 *
 * Claude Code hooks tolerate plain stdout notices.
 * Codex PostToolUse hooks expect JSON when anything is emitted.
 */

import fs from "fs";
import path from "path";
import { loadSettingsEnv } from "./load-env.js";

loadSettingsEnv();

const SERVER_URL = process.argv[2] || process.env.CLAUDE_MCP_SERVER_URL || process.env.AIFY_SERVER_URL || "";
const API_KEY = process.env.CLAUDE_MCP_API_KEY || process.env.AIFY_API_KEY || "";
const tmpDir = process.env.TEMP || process.env.TMP || "/tmp";

async function readHookPayload() {
  if (process.stdin.isTTY) return null;
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const text = Buffer.concat(chunks).toString("utf8").trim();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function fileAgeMs(filePath) {
  try {
    return Date.now() - fs.statSync(filePath).mtimeMs;
  } catch {
    return Number.POSITIVE_INFINITY;
  }
}

function emitNotice(message, hookPayload) {
  if (!message) return;
  if (hookPayload?.hook_event_name === "PostToolUse") {
    process.stdout.write(JSON.stringify({ systemMessage: message }) + "\n");
    return;
  }
  console.log(message);
}

if (!SERVER_URL) process.exit(0);

// If server was unreachable recently, skip entirely (check every 60s)
const DOWN_FILE = path.join(tmpDir, "aify-server-down.ts");
try {
  const lastDown = parseInt(fs.readFileSync(DOWN_FILE, "utf-8"), 10);
  if (Date.now() - lastDown < 60_000) process.exit(0);
} catch { /* no file = never failed */ }

const hookPayload = await readHookPayload();

// Find agent ID from the PID-keyed temp file written by server.js.
// We no longer fall back to {cwd}/.aify-agent — that file is shared
// across sessions in the same directory and causes cross-talk.
let agentId = "";
const SESSION_FILE = path.join(tmpDir, `aify-agent-${process.ppid || ""}`);
let heartbeatAllowed = false;

if (fs.existsSync(SESSION_FILE)) {
  agentId = fs.readFileSync(SESSION_FILE, "utf-8").trim();
  heartbeatAllowed = true;
}
if (!agentId) process.exit(0);

// Rate limit: only check every 10 seconds
const RATE_FILE = path.join(process.env.TEMP || "/tmp", `aify-notify-${agentId}.ts`);
try {
  const lastCheck = parseInt(fs.readFileSync(RATE_FILE, "utf-8"), 10);
  if (Date.now() - lastCheck < 10_000) process.exit(0);
} catch { /* first check */ }
fs.writeFileSync(RATE_FILE, String(Date.now()));

const headers = { "Accept": "application/json" };
if (API_KEY) headers["X-API-Key"] = API_KEY;

try {
  // Check inbox
  const url = `${SERVER_URL}/api/v1/messages/inbox/${agentId}?filter=unread&limit=3&peek=true`;
  const resp = await fetch(url, { headers, signal: AbortSignal.timeout(3000) });
  if (!resp.ok) process.exit(0);
  // Server is up — clear any previous down marker
  try { fs.unlinkSync(DOWN_FILE); } catch {}
  const data = await resp.json();

  if (heartbeatAllowed) {
    fetch(`${SERVER_URL}/api/v1/agents/${agentId}/heartbeat`, {
      method: "POST", headers, signal: AbortSignal.timeout(2000),
    }).catch(() => {});
  }

  if (data.total > 0) {
    const msgs = data.messages || [];
    const hasUrgent = msgs.some(m => m.priority === "urgent" || m.priority === "high");
    const tag = hasUrgent ? " ⚠ URGENT" : "";
    const previews = msgs.map(m => {
      const p = (m.priority && m.priority !== "normal") ? ` [${m.priority.toUpperCase()}]` : "";
      return `  - From ${m.from}${p}: "${m.subject}"`;
    }).join("\n");
    const more = data.total > 3 ? `\n  ...and ${data.total - 3} more` : "";
    emitNotice(`[aify-comms]${tag} ${data.total} unread message(s):\n${previews}${more}\nUse comms_inbox to read them.`, hookPayload);
  }
} catch {
  // Server unreachable — cache the failure so we skip quickly next time
  try { fs.writeFileSync(DOWN_FILE, String(Date.now())); } catch {}
  process.exit(0);
}
