// JSON-RPC framing + session/update → terminal-frame translation for the
// Hermes Agent Client Protocol (ACP) stdio transport.
//
// Wire format reference (live-confirmed 2026-05-23, see
// docs/plans/notes/2026-05-23-hermes-acp-spike.md):
//   * Newline-delimited JSON, one JSON-RPC 2.0 message per line.
//   * Method names use slash-separated form: `session/new`, `session/prompt`,
//     `session/update`, `fs/read_text_file`, etc.
//   * Field names are camelCase: `sessionId`, `mcpServers`, `stopReason`,
//     `protocolVersion`, etc.
//   * `session/update.params.update.sessionUpdate` is the discriminator and
//     its VALUE is snake_case (`agent_message_chunk`, `tool_call`, ...).
//
// This module is pure: no I/O, no spawn. Used by hermes-session.js and by
// the protocol-level tests.

const ANSI = {
  reset: "\x1b[0m",
  bold: "\x1b[1m",
  dim: "\x1b[2m",
  italic: "\x1b[3m",
  red: "\x1b[31m",
  green: "\x1b[32m",
  yellow: "\x1b[33m",
  cyan: "\x1b[36m",
  brightCyan: "\x1b[96m",
};

const MAX_TOOL_INPUT_BRIEF_CHARS = 240;
const MAX_TOOL_RESULT_BRIEF_CHARS = 320;

export const METHODS = Object.freeze({
  INITIALIZE: "initialize",
  AUTHENTICATE: "authenticate",
  SESSION_NEW: "session/new",
  SESSION_LOAD: "session/load",
  SESSION_LIST: "session/list",
  SESSION_RESUME: "session/resume",
  SESSION_FORK: "session/fork",
  SESSION_PROMPT: "session/prompt",
  SESSION_CANCEL: "session/cancel",
  SESSION_CLOSE: "session/close",
  SESSION_SET_MODE: "session/set_mode",
  SESSION_SET_MODEL: "session/set_model",
  SESSION_SET_CONFIG_OPTION: "session/set_config_option",
  SESSION_UPDATE: "session/update",
  SESSION_REQUEST_PERMISSION: "session/request_permission",
  FS_READ_TEXT_FILE: "fs/read_text_file",
  FS_WRITE_TEXT_FILE: "fs/write_text_file",
  TERMINAL_CREATE: "terminal/create",
  TERMINAL_KILL: "terminal/kill",
  TERMINAL_OUTPUT: "terminal/output",
  TERMINAL_RELEASE: "terminal/release",
  TERMINAL_WAIT_FOR_EXIT: "terminal/wait_for_exit",
});

export function encodeRequest(id, method, params) {
  return JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n";
}

export function encodeNotification(method, params) {
  return JSON.stringify({ jsonrpc: "2.0", method, params }) + "\n";
}

export function encodeResponse(id, result) {
  return JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n";
}

export function encodeError(id, code, message) {
  return JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } }) + "\n";
}

// Parse newline-delimited JSON-RPC. Returns { messages, remainder } where
// remainder is the partial trailing line (no terminator yet); the caller
// should re-feed it on the next stdout chunk.
export function parseMessage(buffer) {
  const messages = [];
  const lines = String(buffer || "").split("\n");
  const remainder = lines.pop() ?? "";
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      messages.push(JSON.parse(trimmed));
    } catch {
      // Malformed line — drop silently. A real bridge surfaces this via
      // onEvent("hermes-parse-error", ...) but the parser stays pure.
    }
  }
  return { messages, remainder };
}

function briefJsonInline(value, limit) {
  if (value === undefined || value === null) return "";
  let text;
  if (typeof value === "string") text = value;
  else {
    try { text = JSON.stringify(value); } catch { text = String(value); }
  }
  text = text.replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length <= limit ? text : `${text.slice(0, Math.max(0, limit - 1))}…`;
}

function colorize(color, text) {
  if (!text) return "";
  return `${color}${text}${ANSI.reset}`;
}

function chunkText(content) {
  if (!content) return "";
  if (typeof content === "string") return content;
  if (typeof content.text === "string") return content.text;
  if (Array.isArray(content)) return content.map((p) => (p && p.text) || "").join("");
  return "";
}

// Translate a single `session/update` payload into terminal-frame text.
// `update` is the unwrapped value of `params.update` (the bridge has
// already stripped the JSON-RPC envelope and the sessionId wrapper).
export function formatSessionUpdateAsTerminalFrame(update) {
  if (!update || typeof update !== "object") return "";
  const kind = String(update.sessionUpdate || "");
  switch (kind) {
    case "user_message_chunk": {
      const text = chunkText(update.content);
      if (!text) return "";
      return colorize(ANSI.dim, text);
    }
    case "agent_message_chunk":
      return chunkText(update.content);
    case "agent_thought_chunk": {
      const text = chunkText(update.content);
      if (!text) return "";
      return colorize(ANSI.dim + ANSI.italic, text);
    }
    case "tool_call": {
      const name = String(update.title || update.kind || "tool");
      const brief = briefJsonInline(update.rawInput ?? update.input, MAX_TOOL_INPUT_BRIEF_CHARS);
      const head = colorize(ANSI.yellow, `→ ${name}`);
      const detail = brief ? colorize(ANSI.dim, ` ${brief}`) : "";
      return `\r\n${head}${detail}\r\n`;
    }
    case "tool_call_update":
    case "tool_call_progress": {
      const status = String(update.status || "");
      if (status !== "completed" && status !== "failed") return "";
      const name = String(update.title || "tool");
      const ok = status === "completed";
      const brief = briefJsonInline(
        update.rawOutput ?? update.output,
        MAX_TOOL_RESULT_BRIEF_CHARS,
      );
      const marker = ok
        ? colorize(ANSI.green, `✓ ${name}`)
        : colorize(ANSI.red, `✗ ${name}`);
      const detail = brief ? colorize(ANSI.dim, ` ${brief}`) : "";
      return `${marker}${detail}\r\n`;
    }
    // Variants we deliberately drop. The dashboard Console would treat them
    // as noise. (`usage_update` could be surfaced as a dim footer if the
    // operator asks; today it would clutter the chat-style terminal.)
    case "plan":
    case "agent_plan_update":
    case "available_commands_update":
    case "current_mode_update":
    case "usage_update":
      return "";
    default:
      return "";
  }
}
