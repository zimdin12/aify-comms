#!/usr/bin/env node
/**
 * aify-comms inbox notification + liveness heartbeat, run as a PostToolUse hook.
 *
 * It surfaces new messages to the agent WITHOUT consuming them: the inbox is read with peek, and the
 * ids already shown are remembered so each message is surfaced once. What it prints, and why, is in
 * notify-notice.mjs.
 */

import fs from "fs";
import path from "path";
import { createHash } from "crypto";
import { destinationKeyResolver } from "./aify-service-endpoint.mjs";
import { loadSettingsEnv } from "./load-env.js";
import { readAgentBindingFile } from "./binding-file.js";
import { hookOutput, inboxUrl, noticeText, rememberSeen, unseen } from "./notify-notice.mjs";

// Settings env first: the endpoint and the key may only be named in ~/.claude/settings.local.json.
loadSettingsEnv();

const SERVER_URL = process.argv[2] || process.env.CLAUDE_MCP_SERVER_URL || process.env.AIFY_SERVER_URL || "";
if (!SERVER_URL) process.exit(0);
// The same resolution every other bridge component uses: an exported key first, then the credential
// the service registry names for THIS endpoint (and only this one).
const API_KEY = destinationKeyResolver(SERVER_URL)(SERVER_URL);
const tmpDir = process.env.TEMP || process.env.TMP || "/tmp";
const IS_CLAUDE = Boolean(process.env.CLAUDE_PROJECT_DIR);

// If THIS server was unreachable in the last minute, skip. Keyed by server, so one service being
// down does not mute the hook for another.
const serverKey = createHash("sha256").update(SERVER_URL).digest("hex").slice(0, 12);
const DOWN_FILE = path.join(tmpDir, `aify-server-down-${serverKey}.ts`);
try {
  if (Date.now() - parseInt(fs.readFileSync(DOWN_FILE, "utf-8"), 10) < 60_000) process.exit(0);
} catch { /* no file: never failed */ }

async function readHookPayload() {
  if (process.stdin.isTTY) return null;
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8").trim() || "null");
  } catch {
    return null;
  }
}
const hookPayload = await readHookPayload();

// The agent id comes from the binding file server.js wrote, keyed by the pid this hook and that
// bridge share as a parent.
const agentId = readAgentBindingFile({ pid: process.ppid || "", dir: tmpDir }).agentId;
if (!agentId) process.exit(0);

// At most one check every 10 seconds per agent.
const RATE_FILE = path.join(tmpDir, `aify-notify-${agentId}.ts`);
try {
  if (Date.now() - parseInt(fs.readFileSync(RATE_FILE, "utf-8"), 10) < 10_000) process.exit(0);
} catch { /* first check */ }
fs.writeFileSync(RATE_FILE, String(Date.now()));

const SEEN_FILE = path.join(tmpDir, `aify-notify-seen-${agentId}.json`);
function readSeen() {
  try {
    const ids = JSON.parse(fs.readFileSync(SEEN_FILE, "utf-8"));
    return Array.isArray(ids) ? ids : [];
  } catch {
    return [];
  }
}

const headers = { Accept: "application/json" };
if (API_KEY) headers["X-API-Key"] = API_KEY;

let data;
try {
  // NEVER FOLLOWED: `fetch` re-sends headers on a redirect, so a 302 would hand the key to whatever
  // it points at. A 3xx fails `res.ok` like any other non-2xx.
  const resp = await fetch(inboxUrl(SERVER_URL, agentId), { headers, redirect: "manual", signal: AbortSignal.timeout(3000) });
  if (!resp.ok) process.exit(0);
  try { fs.unlinkSync(DOWN_FILE); } catch {}
  data = await resp.json();
} catch {
  // Only a transport failure marks the server down.
  try { fs.writeFileSync(DOWN_FILE, String(Date.now())); } catch {}
  process.exit(0);
}

// LIVENESS ONLY: refreshes last_seen so an active resident is not reaped as dead. It carries no
// turnBusy field, because status is event-driven (turn start/end), and re-asserting busy on every
// tool call would defeat that.
fetch(`${SERVER_URL}/api/v1/agents/${encodeURIComponent(agentId)}/heartbeat`, {
  method: "POST",
  headers,
  redirect: "manual",
  signal: AbortSignal.timeout(2000),
}).catch(() => {});

const seenIds = readSeen();
const fresh = unseen(data?.messages, seenIds);
if (fresh.length) {
  const notice = noticeText({ messages: fresh, total: Number(data.total) || fresh.length, agentId });
  const output = hookPayload?.hook_event_name === "PostToolUse"
    ? JSON.stringify(hookOutput(notice, { claude: IS_CLAUDE, count: fresh.length }))
    : notice;
  process.stdout.write(output + "\n");
  try { fs.writeFileSync(SEEN_FILE, JSON.stringify(rememberSeen(seenIds, fresh))); } catch {}
}
