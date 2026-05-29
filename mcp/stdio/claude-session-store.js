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
