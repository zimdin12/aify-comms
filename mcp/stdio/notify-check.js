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
import { createHash, randomBytes } from "crypto";
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

// ONE HOOK AT A TIME PER AGENT, IN ORDINARY OVERLAP. The rate file spaces checks ten seconds apart but is no mutex: a
// walk slower than that overlapped the next hook, and the slow one rewrote the record with the older mark it had read,
// so a cue was named again (review of 93de3c1d, reproduced with real hook processes). The record is read, decided on,
// printed and written while this lock is held; a hook that cannot take it prints nothing, and the next check catches up.
//
// RECOVERY IS WEAKER, and says so. A lock whose holder's pid is gone, or older than LOCK_STALE_MS (a pid Windows has
// reused would otherwise read as alive forever), is taken over, so a killed hook cannot silence the agent. A holder
// that was only slow may resume after that: it rereads its token before printing and before writing (`stillOwned`)
// and stops if it has lost the lock, but that check is best-effort, since a pause after it has no bound. Several hooks
// can also take over one dead lock at once. So recovery can repeat a cue and can let a stale holder replace the
// record; how often is unmeasured.
const LOCK_FILE = path.join(tmpDir, `aify-notify-${agentId}.lock`);
const LOCK_STALE_MS = 120_000;
const LOCK_TOKEN = `${process.pid}:${randomBytes(8).toString("hex")}`;
function lockHolderLives(file) {
  try {
    if (Date.now() - fs.statSync(file).mtimeMs > LOCK_STALE_MS) return false;
    const pid = Number(String(fs.readFileSync(file, "utf-8")).split(":")[0]);
    if (!Number.isInteger(pid) || pid <= 0) return false;
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error?.code === "EPERM";
  }
}
function takeLock() {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      fs.writeFileSync(LOCK_FILE, LOCK_TOKEN, { flag: "wx" });
      return true;
    } catch (error) {
      if (error?.code !== "EEXIST" || lockHolderLives(LOCK_FILE)) return false;
      // ponytail: no takeover protocol; several hooks finding one dead lock can all proceed (the recovery note above).
      try { fs.unlinkSync(LOCK_FILE); } catch {}
    }
  }
  return false;
}
function stillOwned() {
  try { return fs.readFileSync(LOCK_FILE, "utf-8") === LOCK_TOKEN; } catch { return false; }
}
if (!takeLock()) process.exit(0);
process.on("exit", () => {
  try { if (stillOwned()) fs.unlinkSync(LOCK_FILE); } catch {}
});

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
  if (!stillOwned()) process.exit(0);
  process.stdout.write(output + "\n");
}
// Kept even when nothing was printed: a queued count that fell must be the one a later rise is measured from.
if (fresh.length || cue.mark.queued !== cueMark.queued || cue.mark.namedSince !== cueMark.namedSince) {
  if (!stillOwned()) process.exit(0);
  try { fs.writeFileSync(SEEN_FILE, JSON.stringify(seenRecord(SESSION, rememberSeen(seenIds, fresh), cue.mark))); } catch {}
}
