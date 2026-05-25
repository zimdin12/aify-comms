import { RuntimeAdapter } from "./base.js";
import { ClaudeController } from "../controllers/claude-controller.js";
import path from "path";
import os from "os";
import { promises as fs } from "fs";

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

  async discoverSessionId() {
    // Plan 4 (2026-05-25): claude stores transcripts at
    // ~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl. Find newest
    // .jsonl across all project subdirs; return session uuid from filename.
    const root = path.join(os.homedir(), ".claude", "projects");
    let projects;
    try { projects = await fs.readdir(root); }
    catch { return null; }
    if (!projects.length) return null;

    let newest = null;
    for (const proj of projects) {
      const projDir = path.join(root, proj);
      let files;
      try { files = await fs.readdir(projDir); }
      catch { continue; }
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
