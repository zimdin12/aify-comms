import { RuntimeAdapter } from "./base.js";
import { CodexController } from "../controllers/codex-controller.js";
import path from "path";
import os from "os";
import { promises as fs } from "fs";

const CODEX_SESSIONS_DIR = path.join(os.homedir(), ".codex", "sessions");
const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
const MAX_WALK_DEPTH = 4;

export class CodexAdapter extends RuntimeAdapter {
  get name() { return "codex"; }
  get displayName() { return "Codex"; }
  get sessionEnvVars() { return ["CODEX_THREAD_ID"]; }

  // Plan 2 capability matrix
  get supportsResident() { return true; }
  get supportsManaged() { return true; }
  get supportsSteering() { return true; }
  get supportsInterrupt() { return true; }
  get supportsMultiClient() { return true; }
  get preferredDeliveryMode() { return "managed-via-wrapper"; }

  controllerFor(opts) {
    return new CodexController(opts);
  }

  diagnosticEnv() {
    const env = super.diagnosticEnv();
    env.AIFY_CODEX_APP_SERVER_URL = String(process.env.AIFY_CODEX_APP_SERVER_URL || "").trim() || "(unset)";
    return env;
  }

  // Plan 4 (2026-05-25): codex storage at ~/.codex/sessions/. Recon found the
  // actual layout is date-sharded — YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl
  // — with a sibling `quarantine-oversized/` flat-file dir. We walk up to 4
  // levels deep, find newest .jsonl by mtime, extract uuid from filename, and
  // fall back to first-line JSON metadata for forward compatibility.
  async discoverSessionId() {
    let newest = null;

    async function walk(p, depth) {
      if (depth > MAX_WALK_DEPTH) return;
      let entries;
      try {
        entries = await fs.readdir(p, { withFileTypes: true });
      } catch {
        return;
      }
      for (const ent of entries) {
        const full = path.join(p, ent.name);
        try {
          if (ent.isDirectory()) {
            await walk(full, depth + 1);
          } else if (ent.isFile() && (ent.name.endsWith(".jsonl") || ent.name.endsWith(".json"))) {
            const stat = await fs.stat(full);
            if (!newest || stat.mtimeMs > newest.mtime) {
              newest = { name: ent.name, full, mtime: stat.mtimeMs };
            }
          }
        } catch {
          // skip unreadable
        }
      }
    }

    try {
      await walk(CODEX_SESSIONS_DIR, 0);
    } catch {
      return null;
    }
    if (!newest) return null;

    // Extract uuid from filename (handles flat <uuid>.jsonl + rollout-...-<uuid>.jsonl)
    const uuidMatch = newest.name.match(UUID_RE);
    if (uuidMatch) return uuidMatch[0];

    // First-line JSON fallback — codex rollouts carry a metadata header line
    try {
      const content = await fs.readFile(newest.full, "utf8");
      const firstLine = content.split(/\r?\n/)[0];
      const parsed = JSON.parse(firstLine);
      const id = parsed.id || parsed.session_id || parsed.sessionId || parsed.thread_id || parsed.threadId;
      if (id && typeof id === "string") return id;
    } catch {
      // not JSON or no id field
    }
    return null;
  }
}
