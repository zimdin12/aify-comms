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

  // The session of a `claude` started from another Claude Code's shell, or "" for any other claude.
  // Such a claude (an agent running `claude -p` or `claude mcp list` from its Bash tool) inherits the
  // agent's AIFY_AGENT_ID and CLAUDE_SESSION_ID, so its bridge resolved the PARENT's session handle and
  // took the agent over by the same-session relaunch rule (sc-manager, 2026-09-26). Claude Code sets
  // CLAUDE_PID in every shell it runs and CLAUDE_CODE_SESSION_ID, its own session, in every stdio MCP
  // server it spawns, and it does not set CLAUDE_PID for an MCP server: CLAUDE_PID reaches a bridge only
  // through a claude that was itself started from a Claude Code shell (read from the claude.exe build of
  // 2026-09-26: the shell environment builder and the stdio MCP spawn). Registering with that session
  // meets the live owner's different handle, which the service refuses (409) instead of taking over.
  sessionWhenStartedFromAClaudeShell(env = process.env) {
    if (!String(env.CLAUDE_PID || "").trim()) return "";
    return String(env.CLAUDE_CODE_SESSION_ID || "").trim();
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
// Harness-injected wrappers that carry no human prompt. `<system-reminder>` is claude's own
// injection channel (session rename, hook output, context notes); the command wrappers are what a
// client-side slash command (/rename, /clear, /help) leaves behind. None of them will ever be
// answered by an assistant turn.
const BOOKKEEPING_BLOCK_RE = /<system-reminder>[\s\S]*?<\/system-reminder>|<command-(?:name|message|args)>[\s\S]*?<\/command-(?:name|message|args)>|<local-command-(?:stdout|stderr)>[\s\S]*?<\/local-command-(?:stdout|stderr)>/g;

// True when a user-role entry is PURE bookkeeping — i.e. once the injected wrappers are removed,
// no actual prompt text remains. A real prompt that merely carries a system-reminder alongside it
// still has its own text and is NOT bookkeeping.
export function isBookkeepingUserMessage(msg) {
  if (!msg) return false;
  const content = msg.content;
  let text = "";
  if (typeof content === "string") {
    text = content;
  } else if (Array.isArray(content)) {
    for (const b of content) {
      if (!b) continue;
      // A tool_result is real turn content — never bookkeeping.
      if (b.type === "tool_result") return false;
      if (typeof b === "string") text += b;
      else if (typeof b.text === "string") text += b.text;
    }
  } else {
    return false;
  }
  return text.replace(BOOKKEEPING_BLOCK_RE, "").trim() === "";
}

// What claude writes when the operator presses Esc: a user message holding only this marker (v0.7.4).
const INTERRUPT_MARKER_RE = /^\[Request interrupted by user(?: for tool use)?\]$/;

/**
 * True for the user message claude appends when a turn is interrupted. Esc fires no hook, and nothing
 * will answer this message, so read as "the human spoke" it latched the agent at `working`; the tail
 * summary flags it so `classify` can call the turn ended.
 */
export function isInterruptMarker(msg) {
  if (!msg) return false;
  const content = msg.content;
  let text = "";
  if (typeof content === "string") text = content;
  else if (Array.isArray(content)) {
    for (const b of content) {
      if (!b || b.type === "tool_result") return false;
      if (typeof b === "string") text += b;
      else if (typeof b.text === "string") text += b.text;
    }
  } else return false;
  return INTERRUPT_MARKER_RE.test(text.trim());
}

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
    // A trailing USER message is normally "the human spoke, the assistant is about to answer" —
    // i.e. IN-FLIGHT. But claude also writes user-role entries that are pure BOOKKEEPING and
    // that nothing will ever answer: a `<system-reminder>` injection, or a client-side slash
    // command's wrapper. Treating one as a pending turn latches the agent at `working` FOREVER —
    // KEEP-FRESH keeps re-arming it, and no assistant reply is ever coming to end it.
    //
    // Live case (2026-07-14): the operator ran `/rename General Manager`. Claude appended a
    // user message whose entire content was
    //     <system-reminder>The user named this session "General Manager"...</system-reminder>
    // and general-manager was stuck `working` from that moment on. This is the same class as
    // NON_MESSAGE_TYPES above (bookkeeping appended after the last real message) — it just
    // arrives wearing a user role, so it slipped through. Skip it and keep scanning back to the
    // last REAL message. A genuine prompt that merely CARRIES a system-reminder still has its
    // own text, so it is untouched.
    if (role === "user" && isBookkeepingUserMessage(msg)) continue;
    if (role === "user" && isInterruptMarker(msg)) {
      return { lastRole: "user", lastStopReason: null, pendingToolUse: false, pendingToolNames: [], interrupted: true };
    }
    const stopReason = msg && typeof msg.stop_reason !== "undefined" ? msg.stop_reason : null;
    let pendingToolUse = false;
    const pendingToolNames = [];
    if (role === "assistant" && msg && Array.isArray(msg.content)) {
      for (const b of msg.content) {
        if (b && b.type === "tool_use") {
          pendingToolUse = true;
          // The tool name lets the Stop gate distinguish an interactive-YIELD tool
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
