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
import { readAgentBindingFile } from "./binding-file.js";
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
// Both this hook and server.js are children of the same Claude/Codex
// process, so process.ppid is the shared key.
let agentId = "";
let heartbeatAllowed = false;
const SESSION_FILE = path.join(tmpDir, `aify-agent-${process.ppid || ""}`);
try {
  const binding = readAgentBindingFile({ pid: process.ppid || "", dir: tmpDir });
  if (binding.agentId) { agentId = binding.agentId; heartbeatAllowed = true; }
} catch { /* file not written yet — agent hasn't registered */ }
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
  // Check inbox. Fetch with bodies (no peek=true) so the hook can surface
  // the full message text inline — hermes/claude reading the system
  // notice can react immediately without an extra comms_inbox tool call
  // (operator-reported 2026-05-24: surface body content so agents auto-
  // process incoming comms_send messages without the extra round trip).
  const url = `${SERVER_URL}/api/v1/messages/inbox/${agentId}?filter=unread&limit=3`;
  const resp = await fetch(url, { headers, signal: AbortSignal.timeout(3000) });
  if (!resp.ok) process.exit(0);
  // Server is up — clear any previous down marker
  try { fs.unlinkSync(DOWN_FILE); } catch {}
  const data = await resp.json();

  if (heartbeatAllowed) {
    // LIVENESS-ONLY heartbeat (pure-event-status change #4, 2026-06-02).
    //
    // The PostToolUse turn_busy RE-PULSE was REMOVED here. It used to re-assert
    // turn_busy=1 on every tool call to keep status='working' alive past the old
    // short status window. With STATUS now PURE-EVENT (the short status window is
    // gone — change #3), re-arming turn_busy on every tool call would defeat the
    // event model: the turn-START event sets working and the turn-END event
    // (claude Stop hook, or the bridge transcript turn-END detector — change #1)
    // clears it; nothing else may set turn_busy. The transcript-growth signal that
    // formerly fed this re-pulse is now repurposed as the #1 turn-END DETECTOR
    // (server.js), never a turn_busy re-arm.
    //
    // What remains is a LIVENESS-ONLY heartbeat (no turnBusy field): it refreshes
    // agents.last_seen so an active resident is not reaped as dead, but it does NOT
    // touch turn_updated_at / turn_busy and so cannot influence derived status.
    fetch(`${SERVER_URL}/api/v1/agents/${agentId}/heartbeat`, {
      method: "POST",
      headers,
      signal: AbortSignal.timeout(2000),
    }).catch(() => {});
  }

  if (data.total > 0) {
    const msgs = data.messages || [];
    const urgent = msgs.filter(m => m.priority === "urgent");
    const high = msgs.filter(m => m.priority === "high");

    // Build a full-body preview block for inline processing — agents can
    // act on the messages without an extra comms_inbox tool-call round-trip
    // (operator-reported 2026-05-24: surface body inline). Cap each
    // message body at 800 chars so the hook output stays readable in TUI;
    // longer messages are truncated with a marker pointing to comms_inbox.
    const MAX_BODY = 800;
    const formatMsg = (m) => {
      const p = (m.priority && m.priority !== "normal") ? ` [${m.priority.toUpperCase()}]` : "";
      const subject = m.subject ? `Subject: ${m.subject}` : "";
      const body = String(m.body || "").trim();
      const truncated = body.length > MAX_BODY
        ? body.slice(0, MAX_BODY) + `\n…[truncated; call comms_inbox(agentId="${agentId}", messageId="${m.id}") for the full body]`
        : body;
      const lines = [
        `=== Message from ${m.from}${p} ===`,
        subject,
        `MessageId: ${m.id}`,
        "",
        truncated,
      ].filter(Boolean);
      return lines.join("\n");
    };
    const previewBlock = msgs.map(formatMsg).join("\n\n");
    const more = data.total > msgs.length ? `\n\n…and ${data.total - msgs.length} more (call comms_inbox to see them).` : "";

    let header;
    if (urgent.length) {
      header = `INCOMING — ${urgent.length} URGENT message(s). Process now before continuing.`;
    } else if (high.length) {
      header = `INCOMING — ${high.length} high-priority message(s). Read and address now.`;
    } else {
      header = `INCOMING — ${data.total} unread message(s). Process these as part of your current work.`;
    }
    const reminderTail = `\n\nReply via comms_send(from="${agentId}", to="<from-agent>", type="response", inReplyTo="<message-id>", ...) so the originator's run threads correctly.`;
    const notice = `${header}\n\n${previewBlock}${more}${reminderTail}`;
    emitNotice(notice, hookPayload);
  }
} catch {
  // Server unreachable — cache the failure so we skip quickly next time
  try { fs.writeFileSync(DOWN_FILE, String(Date.now())); } catch {}
  process.exit(0);
}
