import { RuntimeAdapter } from "./base.js";
import { ClaudeController } from "../controllers/claude-controller.js";
import { readClaudeSessionId } from "../claude-session-store.js";
import path from "path";
import os from "os";
import { promises as fs } from "fs";

// claude project-dir encoding: cwd.replace(/[^a-zA-Z0-9]/g,'-')
function encodeClaudeCwd(cwd) {
  return String(cwd || "").replace(/[^a-zA-Z0-9]/g, "-");
}

export class ClaudeAdapter extends RuntimeAdapter {
  get name() { return "claude-code"; }
  get displayName() { return "Claude Code"; }
  get sessionEnvVars() { return ["CLAUDE_SESSION_ID"]; }

  // Plan 2 capability matrix — see
  // docs/superpowers/specs/2026-05-25-runtime-adapter-plan2-capabilities-design.md
  get supportsResident() { return true; }
  get supportsManaged() { return true; }
  get supportsSteering() { return true; }
  get supportsInterrupt() { return true; }
  get supportsMultiClient() { return true; }
  get preferredDeliveryMode() { return "managed-via-wrapper"; }

  // Symmetric adapter contract (Phase 2: every adapter advertises these).
  // ASYMMETRY(claude): claude mints its own session id (captured via the
  // SessionStart hook → claude-session-store, keyed by AIFY_AGENT_ID), not
  // assignable by aify. aify resumes by feeding the captured id back through
  // the claude-aify wrapper.
  get sessionIdSource() { return "captured"; }

  resumeCommand(sessionId) {
    return `claude-aify --resume ${sessionId}`;
  }

  controllerFor(opts) {
    return new ClaudeController(opts);
  }

  async discoverSessionId(opts = {}) {
    // Session-id truth (2026-05-30, #138): managed claude agents must report
    // their OWN session id, not a machine-global filesystem guess. Precedence:
    //   (a) captured store id (written by claude-session-hook.js, keyed by
    //       AIFY_AGENT_ID — robust on Windows where the hook runs via a shell
    //       so pid/ppid keying is fragile) — the authoritative, per-session
    //       signal that defeats team-in-one-dir contamination.
    //   (b) explicit CLAUDE_SESSION_ID env.
    //   (c) cwd-SCOPED freshest .jsonl in THIS agent's own project dir only.
    //   (d) null. NEVER scan all projects / machine-global freshest.
    const {
      env = process.env,
      homeDir = os.homedir(),
      cwd,
      agentId,
      dir,
    } = opts;

    // (a) captured store id, keyed by agent id
    const resolvedAgentId = agentId || env.AIFY_AGENT_ID || env.AIFY_COMMS_AGENT_ID;
    const captured = readClaudeSessionId({ agentId: resolvedAgentId, dir });
    if (captured) return captured;

    // (b) explicit env
    const envId = String(env.CLAUDE_SESSION_ID || "").trim();
    if (envId) return envId;

    // (c) cwd-scoped freshest within the agent's own project dir only
    const scopedCwd = cwd || env.AIFY_AGENT_CWD || process.cwd();
    const projDir = path.join(homeDir, ".claude", "projects", encodeClaudeCwd(scopedCwd));
    let files;
    try { files = await fs.readdir(projDir); }
    catch { return null; }

    let newest = null;
    for (const f of files) {
      if (!f.endsWith(".jsonl")) continue;
      const full = path.join(projDir, f);
      try {
        const stat = await fs.stat(full);
        if (!stat.isFile()) continue;
        if (!newest || stat.mtimeMs > newest.mtime) {
          newest = { name: f, mtime: stat.mtimeMs };
        }
      } catch { /* skip */ }
    }
    if (!newest) return null;

    const uuidMatch = newest.name.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
    if (uuidMatch) return uuidMatch[0];
    // Fallback: strip extension
    const base = newest.name.replace(/\.jsonl$/, "");
    if (base.length > 0 && base.length < 128) return base;
    return null;
  }

  // Last-modified time (ms epoch) of THIS agent's own claude transcript .jsonl,
  // or 0 if it can't be resolved. Used as a continuous "actively working"
  // signal (operator-reported 2026-05-31, sc-manager): the transcript grows on
  // every token + tool result while a turn runs, so a fresh mtime means claude
  // is mid-turn even during a long GENERATION phase where no PostToolUse hook
  // fires (which otherwise let turn_busy go stale → dashboard wrongly 'online').
  // Scoped to the agent's OWN session id (captured store, then env); when that
  // can't be resolved it returns 0 — NOT a newest-.jsonl-in-dir fallback — so a
  // teammate sharing the cwd never makes this agent look busy. (Does NOT cover a
  // long BLOCKING tool call like a build — claude is idle-waiting then and the
  // transcript doesn't grow.)
  async transcriptMtimeMs(opts = {}) {
    const obs = await this.transcriptStat(opts);
    return obs ? obs.mtimeMs : 0;
  }

  // Like transcriptMtimeMs but returns BOTH mtime and byte size of THIS agent's
  // own transcript, or null if it can't be resolved. Growth-based activity
  // detection (status-liveness fix, 2026-06-01) needs size + mtime together so
  // a "freshly touched but not growing" transcript (e.g. the single final write
  // by the Stop hook at turn end) is NOT mistaken for ongoing generation. Same
  // scoping rules as transcriptMtimeMs: the agent's own session id (captured
  // store, then env); returns null when it can't be resolved — no newest-in-dir
  // fallback, which avoids shared-cwd teammate attribution.
  async transcriptStat(opts = {}) {
    const transcriptPath = this._resolveTranscriptPath(opts);
    if (!transcriptPath) return null;
    try {
      const stat = await fs.stat(transcriptPath);
      if (stat.isFile()) return { mtimeMs: stat.mtimeMs, size: stat.size };
    } catch { /* transcript not present yet */ }
    return null;
  }

  // Resolve the absolute path to THIS agent's OWN claude session .jsonl, or null
  // when the session id can't be resolved. Shared by transcriptStat and
  // transcriptTail so both use identical session scoping (captured store →
  // CLAUDE_SESSION_ID; NEVER a newest-.jsonl-in-dir fallback). Two claude agents
  // can share a cwd; falling back to the newest file would attribute a
  // teammate's transcript to this agent (shared-cwd attribution bug,
  // operator-reported 2026-06-01), so an unresolved id returns null and callers
  // treat that as "unknown / not active".
  _resolveTranscriptPath(opts = {}) {
    const { env = process.env, homeDir = os.homedir(), cwd, agentId, dir } = opts;
    const scopedCwd = cwd || env.AIFY_AGENT_CWD || process.cwd();
    const projDir = path.join(homeDir, ".claude", "projects", encodeClaudeCwd(scopedCwd));
    const resolvedAgentId = agentId || env.AIFY_AGENT_ID || env.AIFY_COMMS_AGENT_ID;
    let sid = "";
    try { sid = readClaudeSessionId({ agentId: resolvedAgentId, dir }) || ""; } catch { sid = ""; }
    if (!sid) sid = String(env.CLAUDE_SESSION_ID || "").trim();
    if (!sid) return null;
    return path.join(projDir, `${sid}.jsonl`);
  }

  // Structural summary of THIS agent's OWN transcript TAIL — the turn-end
  // signal (pure-event-status change #1 rewrite, 2026-06-02). Returns null when
  // the session id can't be resolved or the file can't be read (same scoping as
  // transcriptStat — captured store / env, NEVER a shared-dir fallback); the
  // turn-end detector treats null as "unknown / not-ended" (never false-clear).
  //
  // WHY STRUCTURE, NOT GROWTH: the claude transcript grows PER COMPLETED MESSAGE,
  // not per token, and Task sub-agents write to a SEPARATE subagents/*.jsonl —
  // the PARENT session file is STATIC during a long blocking tool call (build/
  // test >30s), a long generation, or any sub-agent dispatch. So "stopped
  // growing" can NOT distinguish a finished turn from a working one. Instead we
  // read the tail and report the last MESSAGE's structure:
  //   { lastRole, lastStopReason, pendingToolUse }
  // from the real JSONL schema (sampled from a live session 2026-06-02):
  //   - each line is a JSON object with a top-level `type`;
  //   - MESSAGE lines (type "assistant"/"user") carry `message.role`,
  //     `message.stop_reason` (assistant only), and `message.content[].type`
  //     ∈ {text,thinking,tool_use,tool_result};
  //   - NON-message bookkeeping lines (type "last-prompt"/"mode"/
  //     "permission-mode"/"attachment"/"summary"/"system") are appended AFTER the
  //     last assistant message and MUST be skipped to find the last real message.
  // pendingToolUse is true iff that last message is an assistant whose content
  // contains a tool_use block (a tool call awaiting its result — a long build, a
  // pending tool, or a Task sub-agent dispatch). The detector reads ENDED iff
  // lastRole==="assistant" && terminal stop_reason && !pendingToolUse.
  async transcriptTail(opts = {}) {
    const transcriptPath = this._resolveTranscriptPath(opts);
    if (!transcriptPath) return null;
    const tailBytes = Number(opts.tailBytes) > 0 ? Number(opts.tailBytes) : 64 * 1024;
    let text;
    try {
      const fh = await fs.open(transcriptPath, "r");
      try {
        const stat = await fh.stat();
        if (!stat.isFile()) return null;
        const start = Math.max(0, stat.size - tailBytes);
        const len = stat.size - start;
        if (len <= 0) return { lastRole: null, lastStopReason: null, pendingToolUse: false, pendingToolNames: [] };
        const buf = Buffer.alloc(len);
        await fh.read(buf, 0, len, start);
        text = buf.toString("utf8");
      } finally {
        await fh.close();
      }
    } catch {
      return null;
    }
    return summarizeTranscriptTail(text);
  }
}

// Bookkeeping line types claude appends AFTER the last assistant message — they
// carry no message.role / stop_reason and must be skipped when locating the last
// real message.
const NON_MESSAGE_TYPES = new Set([
  "last-prompt", "mode", "permission-mode", "attachment", "summary", "system",
]);

// Parse a transcript tail (a chunk of trailing bytes, possibly starting
// mid-line) into a structural summary { lastRole, lastStopReason, pendingToolUse }.
// Walks lines from the END, skipping bookkeeping lines and any partial first
// line, and returns the first parseable MESSAGE line's structure. When the tail
// holds no message line, returns lastRole:null so the caller treats it as
// "unknown / not-ended".
export function summarizeTranscriptTail(text) {
  const empty = { lastRole: null, lastStopReason: null, pendingToolUse: false, pendingToolNames: [] };
  if (!text) return empty;
  const lines = text.split("\n");
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (!line) continue;
    let obj;
    try { obj = JSON.parse(line); }
    catch { continue; } // partial first line (truncated by the byte window) or junk
    if (!obj || typeof obj !== "object") continue;
    if (NON_MESSAGE_TYPES.has(obj.type)) continue;
    const msg = obj.message;
    const role = msg && msg.role ? msg.role : (obj.type === "assistant" || obj.type === "user" ? obj.type : null);
    if (!role) continue; // not a message line
    const stopReason = msg && typeof msg.stop_reason !== "undefined" ? msg.stop_reason : null;
    let pendingToolUse = false;
    const pendingToolNames = [];
    if (role === "assistant" && msg && Array.isArray(msg.content)) {
      for (const b of msg.content) {
        if (b && b.type === "tool_use") {
          pendingToolUse = true;
          // The tool name lets the detector distinguish an interactive-YIELD tool
          // (AskUserQuestion / ExitPlanMode — blocks awaiting a human, never resumes
          // via PostToolUse) from real work about to run. Without it a turn that
          // yields to a human is read as in-flight and strands the agent at `working`.
          if (b.name) pendingToolNames.push(b.name);
        }
      }
    }
    return { lastRole: role, lastStopReason: stopReason ?? null, pendingToolUse, pendingToolNames };
  }
  return empty;
}
