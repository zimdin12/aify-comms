import fs from "node:fs";
import { RuntimeAdapter } from "./base.js";
import { HermesController } from "../controllers/hermes-controller.js";
import { readSessionIdMarker } from "../hermes-endpoint.js";

export class HermesAdapter extends RuntimeAdapter {
  get name() { return "hermes"; }
  get displayName() { return "Hermes"; }
  get sessionEnvVars() { return ["HERMES_SESSION_ID", "HERMES_SESSION"]; }

  // Capability matrix — gateway-host delivery model (native-session-id,
  // 2026-06-03). Managed/resident hermes delivers like claude-aify, but via a
  // hidden gateway: the per-agent `hermes-managed-host.js run <agent>` loop is
  // the channel-sidecar — it claims dispatch runs over HTTP, opens its own WS to
  // the agent's hidden `hermes dashboard --tui` gateway host, resolves the agent's
  // captured REAL session id via session.active_list, and submits with
  // prompt.submit. A 4009-busy race requeues until turn-end. The retired api_server /
  // hermes-channel.js sidecar and the dead tui_gateway WS bind
  // (aify.session.bind_transport) are no longer in any live path.
  get supportsResident() { return true; }
  get supportsManaged() { return true; }
  // ASYMMETRY(hermes): a managed Hermes submission while the model is active interrupts the
  // current turn. Dispatch therefore queues until turn-end instead of advertising safe mid-turn
  // steering.
  get supportsSteering() { return false; }
  get supportsInterrupt() { return true; }
  get supportsMultiClient() { return true; }
  get preferredDeliveryMode() { return "managed-via-wrapper"; }

  // Symmetric adapter contract (Phase 2: every adapter advertises these).
  // Native-session-id model (2026-06-03): hermes lives in a NORMAL hermes
  // session (its own timestamp id), captured from the visible TUI's
  // active-session file / env — symmetric with claude (UUID, "captured"). The
  // synthetic `aify-<agentId>` pinned id has been retired.
  get sessionIdSource() { return "captured"; }

  // comms_register uses the synchronous adapter seam. Include the active-file
  // source here so a plain visible Hermes TUI can bind its real native session
  // even though Hermes does not export HERMES_SESSION_ID.
  getCurrentSessionId() {
    return this._readActiveSessionFile(process.env) || super.getCurrentSessionId();
  }

  // Operator takeover: the command an operator runs to attach a resident TUI to
  // the agent's pinned session.
  // ASYMMETRY(hermes): resident TUI attaches to the per-agent daemon via
  // HERMES_TUI_GATEWAY_URL — this command must run against THAT agent's daemon,
  // not a machine-global hermes gateway.
  resumeCommand(sessionId) {
    return `hermes --tui --resume ${sessionId}`;
  }

  controllerFor(opts) {
    return new HermesController(opts);
  }

  diagnosticEnv() {
    const env = super.diagnosticEnv();
    // AIFY_HERMES_GATEWAY_URL is the LIVE delivery variable (the WS URL the
    // managed/resident delivery loop connects to — server.js + hermes-managed-host.js).
    // It was dropped from the diagnostic when the dead tui_gateway path was retired
    // (11ba0cd); the later gateway rework made it authoritative again. Surface it.
    env.AIFY_HERMES_GATEWAY_URL = String(process.env.AIFY_HERMES_GATEWAY_URL || "").trim() || "(unset)";
    env.AIFY_HERMES_APISERVER_URL = String(process.env.AIFY_HERMES_APISERVER_URL || "").trim() || "(unset)";
    env.AIFY_HERMES_APISERVER_KEY = process.env.AIFY_HERMES_APISERVER_KEY ? "(set)" : "(unset)";
    return env;
  }

  // Native-session-id model (2026-06-03): a hermes agent's session is its OWN
  // real hermes session id (a timestamp/hash like `20260603_...` or `7afed304`),
  // visible in the TUI — symmetric with claude's UUID / codex's thread. The
  // synthetic `aify-<agentId>` name is RETIRED. Resolve the real id, in order:
  //   (a) the TUI active-session file (AIFY_HERMES_ACTIVE_SESSION_FILE /
  //       HERMES_TUI_ACTIVE_SESSION_FILE) — the live visible session;
  //   (b) the durable env handle (HERMES_SESSION_ID / HERMES_SESSION via
  //       getCurrentSessionId / sessionEnvVars);
  //   (c) the per-agent session-id marker (the last bound real id) as a
  //       fallback so a re-register/relaunch agrees with what launch bound;
  //   (d) null if none. NEVER returns `aify-<agentId>`.
  // (Spec says ""; a falsy sentinel is equivalent for every caller —
  // computeInitialSessionHandle falls through to env on any falsy value — and
  // null keeps the symmetric "no session" contract with the other adapters.)
  // Defensive: the file read is wrapped in try/catch and never throws.
  async discoverSessionId(opts = {}) {
    const { env = process.env, agentId } = opts;

    // (a) PRIMARY — the TUI active-session file (the live visible session). On an
    // explicit `--resume <id>` the wrapper seeds this with <id> (resolve-session
    // --explicit), so it's already authoritative here.
    const active = this._readActiveSessionFile(env);
    if (active) return active;

    // EXPLICIT OPERATOR RESUME (BUG 2, 2026-06-03): when the operator passed
    // `hermes-aify --resume <id>` the wrapper exports
    // AIFY_EXPLICIT_SESSION_HANDLE=true + AIFY_SESSION_HANDLE=<id>. That <id> is
    // AUTHORITATIVE for the registered handle — it MUST win over the (possibly
    // stale) per-agent session marker, so the agent registers the very session the
    // visible TUI resumed, never a stale `aify-hermes-session-<agent>` value. We
    // place it ABOVE the env-session/marker fallbacks but BELOW the active-file so
    // a live active-file (seeded with the same id) still leads.
    const explicitResume = String(env.AIFY_EXPLICIT_SESSION_HANDLE || "").trim().toLowerCase();
    if (explicitResume === "true" || explicitResume === "1") {
      const explicitId = String(env.AIFY_SESSION_HANDLE || "").trim();
      if (explicitId) return explicitId;
    }

    for (const variable of this.sessionEnvVars) {
      const envSession = this.normalizeSessionHandle(env[variable]);
      if (envSession) return envSession;
    }

    const resolvedAgentId = String(
      agentId || env.AIFY_AGENT_ID || env.AIFY_COMMS_AGENT_ID || "",
    ).trim();
    if (resolvedAgentId) {
      const marked = readSessionIdMarker(resolvedAgentId);
      if (marked) return marked;
    }
    return null;
  }

  // Read the real session id from the TUI active-session file. Mirrors the
  // service-side parser (service/runtimes/hermes.py `_read_active_session_file`):
  // a JSON object with `session_id` / `sessionId` / `id`, falling back to the
  // raw file contents. Best-effort: never throws (returns "" on any failure).
  _readActiveSessionFile(env = process.env) {
    const file = String(
      env.AIFY_HERMES_ACTIVE_SESSION_FILE ||
        env.HERMES_TUI_ACTIVE_SESSION_FILE ||
        "",
    ).trim();
    if (!file) return "";
    let raw = "";
    try {
      raw = String(fs.readFileSync(file, "utf8")).trim();
    } catch {
      return ""; // missing/unreadable → no active session
    }
    if (!raw) return "";
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        for (const key of ["session_id", "sessionId", "id"]) {
          const val = parsed[key];
          if (typeof val === "string" && val.trim()) return val.trim();
        }
        return ""; // JSON object with no recognized id key
      }
    } catch {
      // not JSON → treat the raw contents as the id (mirrors python fallback)
    }
    return raw;
  }
}
