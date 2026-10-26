// The per-agent hermes markers comms keeps: the agent's REAL session id, so a relaunch resumes the same
// transcript, and the filename rule for them.
//
// 0.9.10: the gateway port, key and URL markers are gone with the gateway start. aify-env records which port an
// agent's gateway has, and the gateway's own environment carries its URL (hermes-gateway-config.mjs).

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// Temp-root resolution for the session-id marker (and the loop-ready marker, hermes-loop-ready.js).
export function defaultMarkerTmpDir() {
  return process.env.TEMP || process.env.TMP || os.tmpdir();
}

// Sanitize an agentId into a safe filename fragment (same charset rules as the
// pinned session id), so a marker's file name is a valid path component.
// THE OWNER of the hermes filename sanitizer, shared by the loop-ready marker and aify-wrapper's reset too.
//
// NOT THE SAME FUNCTION as `sanitizeAgentId` in `claude-session-store.js`, which shares the name
// and does something different: it keeps dots and substitutes underscores, where this one folds
// runs of anything unsafe into a single dash and trims the ends. `agent.1` becomes `agent.1`
// there and `agent-1` here. Unifying them would repoint existing files on disk, which is a
// migration and not a refactor — so they stay separate and this says why.
export function sanitizeAgentId(agentId) {
  return String(agentId || "")
    .replace(/[^a-zA-Z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

// Agent-keyed REAL hermes session id marker. The native-session-id model
// (2026-06-03): an aify hermes agent lives in a NORMAL hermes session (its own
// timestamp id), symmetric with claude (UUID) / codex (thread). This marker is
// the single source of truth binding agentId -> its real session id, so launch
// (resume the same session), the delivery loop (target it), and the bridge
// (register it) all agree WITHOUT a round-trip — replacing the synthetic
// `aify-<agentId>` session name. Written when the agent first binds (launch or
// comms_register); read on relaunch to resume the SAME session. Agent-keyed
// (never cwd-keyed), so same-folder agents never collide.
function sessionIdMarkerPath(agentId, tempDir) {
  return path.join(tempDir, `aify-hermes-session-${sanitizeAgentId(agentId)}`);
}

// A USABLE hermes session id is alphanumeric + `_` / `-` (e.g.
// `20260604_050351_21531a`, the synthetic `aify-<agentId>`, or a short hex id).
// Reject empty AND — critically — unexpanded shell/template placeholders like
// `${HERMES_SESSION_ID}` or `${AIFY_SESSION_HANDLE}`. Those leak in when a
// config.yaml MCP-env template (`HERMES_SESSION_ID: "${HERMES_SESSION_ID}"`) or a
// wrapper var is UNSET, and if written they POISON the agent→session binding so the
// next launch resumes a nonexistent id and silently starts fresh (the 2026-06-04
// sc-tester incident). Guarding the marker read+write boundary makes a poison value
// a no-op regardless of which caller produced it (defense-in-depth).
export function isUsableSessionId(value) {
  const v = String(value || "").trim();
  return v.length > 0 && /^[A-Za-z0-9_-]+$/.test(v);
}

export function writeSessionIdMarker(agentId, sessionId, { tempDir = defaultMarkerTmpDir() } = {}) {
  const safe = sanitizeAgentId(agentId);
  const id = String(sessionId || "").trim();
  if (!safe || !isUsableSessionId(id)) return false; // never persist a placeholder/garbage id
  try {
    fs.writeFileSync(sessionIdMarkerPath(agentId, tempDir), id);
    return true;
  } catch {
    return false; // best-effort
  }
}

export function readSessionIdMarker(agentId, { tempDir = defaultMarkerTmpDir() } = {}) {
  const safe = sanitizeAgentId(agentId);
  if (!safe) return "";
  try {
    const v = fs.readFileSync(sessionIdMarkerPath(agentId, tempDir), "utf8").trim();
    // Treat a poisoned/placeholder marker (e.g. a pre-fix `${HERMES_SESSION_ID}`
    // written before the write-guard) as ABSENT, so the next launch falls through
    // to active_list resolution / fresh-start instead of resuming garbage.
    return isUsableSessionId(v) ? v : "";
  } catch {
    return "";
  }
}

// Clears the PERSISTENT session-id binding. Call ONLY on a TERMINAL teardown —
// the agent was intentionally removed (410 from /dispatch/claim) — NOT on a
// relaunch reap, or the next launch loses its transcript and starts fresh.
export function clearSessionMarker(agentId, dir = defaultMarkerTmpDir()) {
  const safe = sanitizeAgentId(agentId);
  if (!safe) return;
  try {
    fs.rmSync(path.join(dir, `aify-hermes-session-${safe}`), { force: true });
  } catch {
    /* best-effort: never throw on teardown */
  }
}
