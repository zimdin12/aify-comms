import { RuntimeAdapter } from "./base.js";
import { PiController } from "../controllers/pi-controller.js";
import path from "path";
import os from "os";
import { promises as fs } from "fs";

const PI_SESSIONS_DIR = path.join(os.homedir(), ".omp", "agent", "sessions");
const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

export class PiAdapter extends RuntimeAdapter {
  get name() { return "pi"; }
  get displayName() { return "Pi"; }
  get sessionEnvVars() { return ["PI_SESSION_ID", "OMP_SESSION_ID", "AIFY_PI_SESSION_ID"]; }

  // Plan 2 capability matrix — the pi delivery flip:
  //   resident=false because omp --mode rpc is single-client stdio.
  //   preferredDeliveryMode pins pi to the unified wrapper-backing path.
  get supportsResident() { return false; }
  get supportsManaged() { return true; }
  get supportsSteering() { return true; }
  get supportsInterrupt() { return true; }
  get supportsMultiClient() { return false; }
  get preferredDeliveryMode() { return "managed-via-wrapper"; }

  controllerFor(opts) {
    // Plan 2 pi flip: resident pi is no longer supported — return null
    // so launchRuntimeRun rejects with a clear error.
    const mode = String(
      opts?.executionMode ||
      opts?.run?.executionMode ||
      opts?.agentInfo?.sessionMode ||
      "managed",
    ).trim().toLowerCase();
    if (mode === "resident") return null;
    return new PiController(opts);
  }

  // Plan 4 (2026-05-25): pi storage at ~/.omp/agent/sessions/<project-key>/
  // <timestamp>_<uuid>.jsonl. The session id is the UUID embedded in the
  // filename; the first JSON line of the file also carries an `id` field with
  // the same value (fallback). We scan one level deep — flat files at root
  // are also tolerated for forward compatibility.
  async discoverSessionId() {
    let newest = null;
    const candidates = await this._collectCandidates(PI_SESSIONS_DIR);
    if (candidates === null) return null;
    for (const c of candidates) {
      if (!newest || c.mtime > newest.mtime) newest = c;
    }
    if (!newest) return null;
    const uuidMatch = newest.name.match(UUID_RE);
    if (uuidMatch) return uuidMatch[0];
    // First-line JSON fallback (id / session_id / sessionId)
    try {
      const content = await fs.readFile(newest.full, "utf8");
      const firstLine = content.split(/\r?\n/)[0];
      const parsed = JSON.parse(firstLine);
      const id = parsed.id || parsed.session_id || parsed.sessionId;
      if (id && typeof id === "string") return id;
    } catch {
      // not JSON or no id field
    }
    return null;
  }

  // Returns array of {name, full, mtime} for jsonl/json files one level deep
  // OR at the top level. Returns null if the root dir doesn't exist.
  async _collectCandidates(rootDir) {
    let topEntries;
    try {
      topEntries = await fs.readdir(rootDir, { withFileTypes: true });
    } catch {
      return null;
    }
    const out = [];
    for (const ent of topEntries) {
      const full = path.join(rootDir, ent.name);
      if (ent.isFile()) {
        await this._pushIfSession(out, ent.name, full);
      } else if (ent.isDirectory()) {
        let subEntries;
        try {
          subEntries = await fs.readdir(full, { withFileTypes: true });
        } catch {
          continue;
        }
        for (const sub of subEntries) {
          if (!sub.isFile()) continue;
          await this._pushIfSession(out, sub.name, path.join(full, sub.name));
        }
      }
    }
    return out;
  }

  async _pushIfSession(out, name, full) {
    // Only consider files that look like session payloads (jsonl/json) and
    // carry a uuid in their filename — keeps unrelated files (locks, indexes)
    // out of the scan.
    if (!/\.(jsonl|json)$/i.test(name)) return;
    if (!UUID_RE.test(name)) return;
    try {
      const stat = await fs.stat(full);
      if (!stat.isFile()) return;
      out.push({ name, full, mtime: stat.mtimeMs });
    } catch {
      // skip unreadable
    }
  }
}
