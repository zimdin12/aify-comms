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
}
