import { RuntimeAdapter } from "./base.js";
import { HermesController } from "../controllers/hermes-controller.js";
import { buildSessionMostRecentFrame } from "../hermes-gateway-protocol.js";
import path from "path";
import os from "os";
import { promises as fs } from "fs";
import WebSocket from "ws";

const HERMES_GATEWAY_TIMEOUT_MS = 3000;
const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

export class HermesAdapter extends RuntimeAdapter {
  get name() { return "hermes"; }
  get displayName() { return "Hermes"; }
  get sessionEnvVars() { return ["HERMES_SESSION_ID", "HERMES_SESSION"]; }

  // Plan 2 capability matrix
  get supportsResident() { return true; }
  get supportsManaged() { return true; }
  get supportsSteering() { return true; }
  get supportsInterrupt() { return true; }
  get supportsMultiClient() { return true; }
  get preferredDeliveryMode() { return "managed-via-wrapper"; }

  controllerFor(opts) {
    return new HermesController(opts);
  }

  diagnosticEnv() {
    const env = super.diagnosticEnv();
    env.AIFY_HERMES_GATEWAY_URL = String(process.env.AIFY_HERMES_GATEWAY_URL || "").trim() || "(unset)";
    return env;
  }

  // Hermes session discovery:
  // - active-session file: current visible TUI wrote this after create/resume
  // - env handle: explicit --resume / bridge-owned managed wrapper handle
  // - filesystem fallback: legacy non-gateway sessions only
  //
  // Do not use gateway session.most_recent for live gateway sessions. It reads
  // historical DB state before the visible TUI has necessarily attached and can
  // bind a resident identity to a session that cannot be visibly woken.
  async discoverSessionId() {
    // The visible TUI writes this file after its real session create/resume.
    // Prefer it over parent-shell env, which can be stale before the TUI has
    // actually attached. The value may be a durable key (resume) or active sid
    // (new session); the resident controller's visible bind accepts both.
    const activeFileSession = await this._readActiveSessionFile();
    if (activeFileSession) return activeFileSession;

    // `session.resume` returns a short in-memory gateway sid for the bridge's
    // WebSocket. That sid is not a durable Hermes session key, so never let
    // gateway discovery overwrite the wrapper's real HERMES_SESSION_ID.
    const envSession = this.getCurrentSessionId();
    if (envSession) return envSession;
    const gw = String(process.env.AIFY_HERMES_GATEWAY_URL || "").trim();
    if (gw && /^wss?:\/\//i.test(gw)) {
      return null;
    }
    return this._scanHermesSessionsDir();
  }

  async _readActiveSessionFile() {
    const file = String(process.env.AIFY_HERMES_ACTIVE_SESSION_FILE || "").trim();
    if (!file) return null;
    let raw = "";
    try { raw = await fs.readFile(file, "utf8"); }
    catch { return null; }
    try {
      const parsed = JSON.parse(raw);
      const id = parsed?.session_id || parsed?.sessionId || parsed?.id;
      return typeof id === "string" && id.trim() ? id.trim() : null;
    } catch {
      const id = raw.trim();
      return id ? id : null;
    }
  }

  async _queryGatewayMostRecent(gatewayUrl) {
    return new Promise((resolve) => {
      let settled = false;
      let ws;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        try { ws?.close(); } catch { /* noop */ }
        resolve(null);
      }, HERMES_GATEWAY_TIMEOUT_MS);

      const finish = (val) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        try { ws?.close(); } catch { /* noop */ }
        resolve(val);
      };

      try {
        ws = new WebSocket(gatewayUrl);
      } catch {
        clearTimeout(timer);
        return resolve(null);
      }

      ws.on("open", () => {
        try {
          ws.send(JSON.stringify(buildSessionMostRecentFrame({ id: 1 })));
        } catch { /* noop */ }
      });

      ws.on("message", (data) => {
        if (settled) return;
        let msg;
        try { msg = JSON.parse(data.toString()); }
        catch { return; }
        if (msg?.id !== 1) return;
        const result = msg.result;
        let id = null;
        if (typeof result === "string") id = result;
        else if (result && typeof result === "object") {
          id = result.session_id || result.sessionId || result.id || null;
        }
        finish(typeof id === "string" && id ? id : null);
      });

      ws.on("error", () => finish(null));
      ws.on("close", () => finish(null));
    });
  }

  async _scanHermesSessionsDir() {
    const dir = path.join(os.homedir(), ".hermes", "sessions");
    let entries;
    try { entries = await fs.readdir(dir); }
    catch { return null; }
    if (!entries.length) return null;

    let newest = null;
    for (const name of entries) {
      const full = path.join(dir, name);
      try {
        const stat = await fs.stat(full);
        if (!stat.isFile()) continue;
        if (!newest || stat.mtimeMs > newest.mtime) {
          newest = { name, full, mtime: stat.mtimeMs };
        }
      } catch { /* skip */ }
    }
    if (!newest) return null;

    // Prefer uuid from filename, then derive from basename, then first-line JSON metadata.
    const uuidMatch = newest.name.match(UUID_RE);
    if (uuidMatch) return uuidMatch[0];

    const baseName = newest.name.replace(/\.jsonl?$/, "");
    if (baseName.length > 0 && baseName.length < 128) return baseName;

    try {
      const content = await fs.readFile(newest.full, "utf8");
      const firstLine = content.split(/\r?\n/)[0];
      const parsed = JSON.parse(firstLine);
      const id = parsed.session_id || parsed.sessionId || parsed.id;
      if (id && typeof id === "string") return id;
    } catch { /* not JSON */ }
    return null;
  }
}
