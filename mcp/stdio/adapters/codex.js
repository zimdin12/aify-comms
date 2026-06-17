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

  // Symmetric adapter contract (Phase 2: every adapter advertises these).
  // codex session ids come from a prior rollout that aify RESUMES by handing
  // the id back to the CLI — internally the codex-aify wrapper runs
  // `codex resume --include-non-interactive <id>` (see install.sh codex
  // branch). The load-bearing part is the POSITIONAL <id>: that is what
  // resumes the rollout. `--include-non-interactive` is a no-op when an
  // explicit id is given (it only matters for id-less resume), kept only for
  // parity with the interactive path. The operator takeover command is the
  // wrapper form.
  // ASYMMETRY(codex): the wrapper rewrites `--resume <id>` into the codex
  // `resume` subcommand; the operator-facing command stays the symmetric
  // `<wrapper> --resume <id>` form.
  get sessionIdSource() { return "resume"; }

  resumeCommand(sessionId) {
    return `codex-aify --resume ${sessionId}`;
  }

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
  // NOTE: in the normal codex-aify wrapper path this filesystem-walk branch is
  // SHADOWED — the app-server early-return below (AIFY_CODEX_APP_SERVER_URL set)
  // takes over, and the live thread id is resolved by discoverCodexLiveThreadId
  // in server.js. The walk only runs when no app-server URL is configured.
  async discoverSessionId() {
    const appServerUrl = String(process.env.AIFY_CODEX_APP_SERVER_URL || "").trim();
    if (appServerUrl) {
      return this.getCurrentSessionId() || null;
    }

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

  // WS-4b (2026-06-17): resident-codex turn detector support. Resident codex has
  // only the UserPromptSubmit/Stop hooks for turn state (inert on old CLIs / lost on
  // a dropped Stop), unlike claude (transcript detector) and hermes (gateway
  // detector). Reading the rollout tail gives the SAME structural summary the generic
  // turn detector consumes ({ lastRole, lastStopReason, pendingToolUse }), so
  // startClaudeTurnEndDetector can drive /turn-start//turn-end from process truth.
  async transcriptTail(opts = {}) {
    const file = await this._resolveRolloutPath(opts);
    if (!file) return null;
    const tailBytes = Number(opts.tailBytes) > 0 ? Number(opts.tailBytes) : 64 * 1024;
    try {
      const fh = await fs.open(file, "r");
      try {
        const stat = await fh.stat();
        if (!stat.isFile()) return null;
        const start = Math.max(0, stat.size - tailBytes);
        const len = stat.size - start;
        if (len <= 0) return { lastRole: null, lastStopReason: null, pendingToolUse: false };
        const buf = Buffer.alloc(len);
        await fh.read(buf, 0, len, start);
        return summarizeCodexRolloutTail(buf.toString("utf8"));
      } finally {
        await fh.close();
      }
    } catch {
      return null;
    }
  }

  // Resolve the active rollout file: prefer the one whose filename uuid matches the
  // agent's current session id; else the newest .jsonl by mtime (the live session is
  // the one being appended). Bounded walk of the date-sharded sessions layout.
  async _resolveRolloutPath(opts = {}) {
    const wantId = String(
      (typeof this.getCurrentSessionId === "function" ? this.getCurrentSessionId() : "") ||
      opts.sessionId || ""
    ).trim().toLowerCase();
    let newest = null;
    let byId = null;
    async function walk(p, depth) {
      if (depth > MAX_WALK_DEPTH) return;
      let entries;
      try { entries = await fs.readdir(p, { withFileTypes: true }); } catch { return; }
      for (const ent of entries) {
        const full = path.join(p, ent.name);
        try {
          if (ent.isDirectory()) {
            await walk(full, depth + 1);
          } else if (ent.isFile() && ent.name.endsWith(".jsonl")) {
            const stat = await fs.stat(full);
            if (!newest || stat.mtimeMs > newest.mtime) newest = { full, mtime: stat.mtimeMs };
            if (wantId) {
              const m = ent.name.match(UUID_RE);
              if (m && m[0].toLowerCase() === wantId && (!byId || stat.mtimeMs > byId.mtime)) {
                byId = { full, mtime: stat.mtimeMs };
              }
            }
          }
        } catch { /* skip unreadable */ }
      }
    }
    try { await walk(CODEX_SESSIONS_DIR, 0); } catch { return null; }
    return (byId && byId.full) || (newest && newest.full) || null;
  }
}

// Parse a codex rollout JSONL tail into the structural summary the generic turn
// detector (turn-end-detector.js) consumes: { lastRole, lastStopReason,
// pendingToolUse }. Codex rollout lines are { type, payload }: a turn ENDS with an
// `event_msg`/`task_complete` line; it is IN-FLIGHT while later `response_item`
// (message / function_call / reasoning) or an `event_msg`/`task_started` line has no
// following completion. Walk from the END; the first MEANINGFUL line decides:
//   event_msg/task_complete -> ENDED   ({assistant, end_turn, no pending tool})
//   event_msg/task_started  -> IN-FLIGHT (a turn began, nothing after it)
//   response_item/message   -> IN-FLIGHT (user just submitted / assistant mid-turn)
//   response_item/<tool|reasoning> -> IN-FLIGHT (model still owes work)
// token_count / *_delta / other bookkeeping event_msg lines are skipped. A tail with
// no meaningful line -> { lastRole:null } (unknown — the detector treats it as no
// change, so a transient unreadable tick never false-flips state).
const CODEX_BOOKKEEPING_EVENTS = new Set([
  "token_count", "agent_message_delta", "agent_reasoning_delta",
  "agent_reasoning_raw_content_delta", "background_event",
]);

export function summarizeCodexRolloutTail(text) {
  const empty = { lastRole: null, lastStopReason: null, pendingToolUse: false };
  if (!text) return empty;
  const lines = String(text).split("\n");
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (!line) continue;
    let o;
    try { o = JSON.parse(line); } catch { continue; } // partial first line / junk
    if (!o || typeof o !== "object") continue;
    const payload = o.payload && typeof o.payload === "object" ? o.payload : null;
    const ptype = payload ? payload.type : null;
    if (o.type === "event_msg") {
      if (ptype === "task_complete") {
        return { lastRole: "assistant", lastStopReason: "end_turn", pendingToolUse: false };
      }
      if (ptype === "task_started") {
        return { lastRole: "user", lastStopReason: null, pendingToolUse: false };
      }
      if (CODEX_BOOKKEEPING_EVENTS.has(ptype)) continue; // skip, keep walking
      continue; // any other event_msg: not a turn-state edge, keep walking
    }
    if (o.type === "response_item" && payload) {
      if (ptype === "message") {
        const role = payload.role === "assistant" ? "assistant" : "user";
        // An assistant message with no following task_complete is still mid-turn.
        return { lastRole: role, lastStopReason: null, pendingToolUse: role === "assistant" };
      }
      // function_call / function_call_output / reasoning / local_shell_call / etc.
      return { lastRole: "assistant", lastStopReason: null, pendingToolUse: true };
    }
    // session metadata header or unknown line type -> not turn-state, keep walking
  }
  return empty;
}
