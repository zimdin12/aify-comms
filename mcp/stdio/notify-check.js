#!/usr/bin/env node
/**
 * aify-claude inbox notification checker.
 * Called by Claude Code PostToolUse hook to check for unread messages.
 *
 * Reads agent ID from .aify-agent file in cwd (written by cc_register).
 * If unread messages exist, prints a notification to stdout which
 * gets injected into the Claude Code session context.
 *
 * Usage: node notify-check.js [server-url]
 */

import fs from "fs";
import path from "path";

const SERVER_URL = process.argv[2] || process.env.CLAUDE_MCP_SERVER_URL || "";
const API_KEY = process.env.CLAUDE_MCP_API_KEY || "";
const AGENT_FILE = path.join(process.cwd(), ".aify-agent");

// Skip if no server URL (local mode) or no agent file
if (!SERVER_URL) process.exit(0);
if (!fs.existsSync(AGENT_FILE)) process.exit(0);

const agentId = fs.readFileSync(AGENT_FILE, "utf-8").trim();
if (!agentId) process.exit(0);

// Rate limit: only check every 30 seconds
const RATE_FILE = path.join(process.env.TEMP || "/tmp", `aify-notify-${agentId}.ts`);
try {
  const lastCheck = parseInt(fs.readFileSync(RATE_FILE, "utf-8"), 10);
  if (Date.now() - lastCheck < 30_000) process.exit(0);
} catch { /* first check */ }
fs.writeFileSync(RATE_FILE, String(Date.now()));

// Check inbox
const headers = { "Accept": "application/json" };
if (API_KEY) headers["X-API-Key"] = API_KEY;

try {
  const url = `${SERVER_URL}/api/v1/messages/inbox/${agentId}?filter=unread&limit=3`;
  const resp = await fetch(url, { headers, signal: AbortSignal.timeout(3000) });
  if (!resp.ok) process.exit(0);
  const data = await resp.json();

  if (data.total > 0) {
    const msgs = data.messages || [];
    const previews = msgs.map(m => `  - From ${m.from}: "${m.subject}"`).join("\n");
    const more = data.total > 3 ? `\n  ...and ${data.total - 3} more` : "";
    console.log(`[aify-claude] ${data.total} unread message(s):\n${previews}${more}\nUse cc_inbox to read them.`);
  }
} catch {
  // Silently fail — don't disrupt the session
  process.exit(0);
}
