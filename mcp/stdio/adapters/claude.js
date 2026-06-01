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
  // Scoped to the agent's OWN session id (captured store, then env) so a
  // teammate sharing the cwd never makes this agent look busy; falls back to the
  // freshest .jsonl in the agent's own project dir only. (Does NOT cover a long
  // BLOCKING tool call like a build — claude is idle-waiting then and the
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
  // scoping rules as transcriptMtimeMs (agent's own session id, then env, then
  // scoped-newest within the agent's own project dir only).
  async transcriptStat(opts = {}) {
    const { env = process.env, homeDir = os.homedir(), cwd, agentId, dir } = opts;
    const scopedCwd = cwd || env.AIFY_AGENT_CWD || process.cwd();
    const projDir = path.join(homeDir, ".claude", "projects", encodeClaudeCwd(scopedCwd));
    const resolvedAgentId = agentId || env.AIFY_AGENT_ID || env.AIFY_COMMS_AGENT_ID;
    let sid = "";
    try { sid = readClaudeSessionId({ agentId: resolvedAgentId, dir }) || ""; } catch { sid = ""; }
    if (!sid) sid = String(env.CLAUDE_SESSION_ID || "").trim();
    if (sid) {
      try {
        const stat = await fs.stat(path.join(projDir, `${sid}.jsonl`));
        if (stat.isFile()) return { mtimeMs: stat.mtimeMs, size: stat.size };
      } catch { /* fall through to scoped-newest */ }
    }
    let files;
    try { files = await fs.readdir(projDir); } catch { return null; }
    let newest = null;
    for (const f of files) {
      if (!f.endsWith(".jsonl")) continue;
      try {
        const st = await fs.stat(path.join(projDir, f));
        if (st.isFile() && (!newest || st.mtimeMs > newest.mtimeMs)) {
          newest = { mtimeMs: st.mtimeMs, size: st.size };
        }
      } catch { /* skip */ }
    }
    return newest;
  }
}
