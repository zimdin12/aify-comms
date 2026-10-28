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
import { envAgentId } from "./bound-agent-id.mjs";
import {
  channelSessionOptsIn, collectUnseen, cueMarkForSession, hookOutput, inboxUrl, nextCue, noticeText, queuedCueText,
  rememberSeen, seenForSession, seenRecord,
} from "./notify-notice.mjs";

// Settings env first: the endpoint and the key may only be named in ~/.claude/settings.local.json.
loadSettingsEnv();

// AIFY_COMMS_URL last: it is the only one claude-aify exports into the harness, so without it every
// launched Claude agent's hook stopped here (2026-10-14), as agent-state-event.mjs already allows for.
const SERVER_URL = process.argv[2] || process.env.CLAUDE_MCP_SERVER_URL || process.env.AIFY_SERVER_URL
  || process.env.AIFY_COMMS_URL || "";
if (!SERVER_URL) process.exit(0);
// The same resolution every other bridge component uses: an exported key first, then the credential
// the service registry names for THIS endpoint (and only this one).
const API_KEY = destinationKeyResolver(SERVER_URL)(SERVER_URL);
const tmpDir = process.env.TEMP || process.env.TMP || "/tmp";

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

// A subagent or in-process teammate fires its parent's hooks under the parent's AIFY_AGENT_ID, and Claude Code
// names it with agent_id (a root `--agent` session carries only agent_type). Its notice would reach the child and
// be recorded as shown to the parent, so the parent's own next tool call surfaces it instead.
if (String(hookPayload?.agent_id || "").trim()) process.exit(0);

// The agent id is the one the launcher exports and the harness hands to its hooks. The hook once read the
// binding file keyed by its parent pid instead, which proves nothing about the process now holding that
// pid: on Windows the parent is a shell, and on 2026-10-13 no live agent's hook found itself while two dead
// agents stayed available, their last_seen matching this hook's rate files to the second.
const agentId = envAgentId();
if (!agentId) process.exit(0);

// At most one check every 10 seconds per agent.
const RATE_FILE = path.join(tmpDir, `aify-notify-${agentId}.ts`);
try {
  if (Date.now() - parseInt(fs.readFileSync(RATE_FILE, "utf-8"), 10) < 10_000) process.exit(0);
} catch { /* first check */ }
fs.writeFileSync(RATE_FILE, String(Date.now()));

const SEEN_FILE = path.join(tmpDir, `aify-notify-seen-${agentId}.json`);
// The session the notices are for: Claude Code names it in the hook payload; elsewhere the harness
// process stands in for it, which a relaunch replaces too.
const SESSION = String(hookPayload?.session_id || process.ppid || "");
function readStored() {
  try {
    return JSON.parse(fs.readFileSync(SEEN_FILE, "utf-8"));
  } catch {
    return null;
  }
}

const headers = { Accept: "application/json" };
if (API_KEY) headers["X-API-Key"] = API_KEY;
const queuedChannel = channelSessionOptsIn(process.env);

async function fetchPage(offset) {
  // NEVER FOLLOWED: `fetch` re-sends headers on a redirect, so a 302 would hand the key to whatever
  // it points at. A 3xx fails `res.ok` like any other non-2xx.
  const resp = await fetch(inboxUrl(SERVER_URL, agentId, offset, { queuedChannel }), {
    headers, redirect: "manual", signal: AbortSignal.timeout(3000),
  });
  if (!resp.ok) return null;
  try { fs.unlinkSync(DOWN_FILE); } catch {}
  return resp.json();
}

const stored = readStored();
const seenIds = seenForSession(stored, SESSION);
const cueMark = cueMarkForSession(stored, SESSION);
let collected;
try {
  collected = await collectUnseen(fetchPage, seenIds);
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

const { fresh, total, queued, unreadTotal } = collected;
// A queued message is never shown as a body: if its run fails, the message returns to the paged population and its
// body is shown then. What the session is told about the queued ones is `nextCue`'s.
const cue = nextCue(queued, cueMark);
if (fresh.length || cue.due) {
  const parts = [];
  if (fresh.length) parts.push(noticeText({ messages: fresh, total: total || fresh.length, agentId }));
  if (cue.due) parts.push(queuedCueText({ named: cue.named, queuedTotal: queued.total, unreadTotal, agentId }));
  const notice = parts.join("\n\n");
  const output = hookPayload?.hook_event_name === "PostToolUse"
    ? JSON.stringify(hookOutput(notice, { count: fresh.length + cue.named.length }))
    : notice;
  process.stdout.write(output + "\n");
}
// Kept even when nothing was printed: a queued count that fell must be the one a later rise is measured from.
if (fresh.length || cue.mark.queued !== cueMark.queued || cue.mark.namedSince !== cueMark.namedSince) {
  try { fs.writeFileSync(SEEN_FILE, JSON.stringify(seenRecord(SESSION, rememberSeen(seenIds, fresh), cue.mark))); } catch {}
}
