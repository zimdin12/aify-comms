import fs from "fs";
import os from "os";
import path from "path";

// Per-session capture of claude's OWN session id, keyed by AGENT ID.
// A claude SessionStart/UserPromptSubmit hook (claude-session-hook.js) writes
// the authoritative session_id here, keyed by AIFY_AGENT_ID (exported by the
// managed wrapper into claude's env). The bridge reads it back by the same
// agentId. agentId keying is robust on Windows where the hook runs via a shell
// (so process.ppid is the shell, not claude, and pid/ppid keying is fragile).
// This defeats the machine-global filesystem guess that caused
// cross-contamination (#138).

function storeBaseDir(dir = "") {
  return dir || process.env.TEMP || process.env.TMP || os.tmpdir();
}

// SAME NAME, DIFFERENT FUNCTION from `sanitizeAgentId` in `hermes-endpoint.js`, and deliberately
// so: this one keeps dots and substitutes underscores, that one folds runs into a dash and trims.
// `agent.1` maps to `agent.1` here and `agent-1` there. They name files in different stores, so
// unifying them would repoint existing files on disk — a migration, not a refactor. Recorded
// because a shared name across four modules is exactly what made this look like one function.
function sanitizeAgentId(agentId) {
  return String(agentId).replace(/[^a-zA-Z0-9._-]/g, "_");
}

export function claudeSessionStorePath(agentId, dir = "") {
  return path.join(storeBaseDir(dir), `aify-claude-session-${sanitizeAgentId(agentId)}.json`);
}

export function writeClaudeSessionId({ sessionId, agentId, dir = "" } = {}) {
  const id = String(agentId || "").trim();
  if (!id) return false; // no-op without an agent id to key by
  const payload = {
    sessionId: String(sessionId || "").trim(),
    agentId: id,
    updatedAt: new Date().toISOString(),
  };
  try {
    const file = claudeSessionStorePath(id, dir);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(payload));
    return file;
  } catch {
    // best-effort: never let session capture break the caller
    return false;
  }
}

// ── PID-keyed capture: the session id we know BEFORE we know who we are ──────
//
// A session launched without `--aify-agent` has no AIFY_AGENT_ID, so the hook above
// has nothing to key by and used to DROP the session id entirely. The bridge then
// couldn't resolve its own transcript even after `comms_register` told it its agent
// id — which is why registering did not turn status on (a genuinely bad UX, and the
// general-manager incident). So capture the session id keyed by the CLAUDE PROCESS
// (the hook's ppid == the bridge's ppid — both are children of the same claude), and
// let `comms_register` PROMOTE it to the agent-keyed store the moment identity arrives.
//
// Deliberately a DIFFERENT filename prefix from the agent-keyed store: `claude-aify`'s
// handle->agent recovery globs `aify-claude-session-*.json` and must never match one of
// these and "recover" an agent id of `pid-1234`.
//
// Windows caveat: the hook there can run via a shell, making its ppid the shell rather
// than claude — the capture then keys on the wrong pid and simply misses (we fall back
// to today's behaviour + the wrapper's loud warning). Correct on Linux/macOS/WSL.
export function claudeSessionPidCapturePath(pid, dir = "") {
  const key = Number(pid) || 0;
  return path.join(storeBaseDir(dir), `aify-claude-pidsession-${key}.json`);
}

export function writeCapturedClaudeSessionIdForPid({ sessionId, pid, dir = "" } = {}) {
  const sid = String(sessionId || "").trim();
  const key = Number(pid) || 0;
  if (!sid || !key) return false;
  try {
    const file = claudeSessionPidCapturePath(key, dir);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify({ sessionId: sid, pid: key, updatedAt: new Date().toISOString() }));
    return file;
  } catch {
    return false;
  }
}

export function readCapturedClaudeSessionIdForPid({ pid, dir = "" } = {}) {
  const key = Number(pid) || 0;
  if (!key) return null;
  try {
    const raw = fs.readFileSync(claudeSessionPidCapturePath(key, dir), "utf-8").trim();
    if (!raw) return null;
    const sessionId = String(JSON.parse(raw)?.sessionId || "").trim();
    return sessionId || null;
  } catch {
    return null;
  }
}

export function readClaudeSessionId({ agentId, dir = "" } = {}) {
  const id = String(agentId || "").trim();
  if (!id) return null;
  try {
    const raw = fs.readFileSync(claudeSessionStorePath(id, dir), "utf-8").trim();
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    const sessionId = String(parsed?.sessionId || "").trim();
    return sessionId || null;
  } catch {
    return null;
  }
}
