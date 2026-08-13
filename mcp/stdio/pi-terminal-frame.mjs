// How a pi event RENDERS as a terminal frame.
//
// The dashboard console shows a real terminal for every runtime, including pi — which does not speak
// terminal, it emits structured JSON events. This module is the translation: an event in, ANSI-coloured
// terminal text out. It is the whole reason a pi agent looks like the others in the console.
//
// IT IS PURE, AND THAT IS WHY IT IS A MODULE. `pi-session.js` is a 960-line class managing a child process,
// its RPC, its idle timers and a session pool; none of that is needed to decide how a tool result should
// read. Extracted, the rendering can be tested by calling it — which is how the console's output is now
// checked instead of by reading the class's source.
//
// EVERYTHING IS BOUNDED. Tool inputs, tool results, captured errors and the frame itself each have a
// character cap and a truncation marker, because these strings go to a browser terminal and a model's
// context. An unbounded tool result is not a rendering bug — it is a frame that costs whoever reads it.
//
// THE SAME TWO HELPERS EXIST IN `hermes-acp-protocol.js`, and they are NOT shared. `colorize` is
// byte-identical there and `briefJsonInline` is the same logic in a different brace style; `ANSI`,
// `MAX_TOOL_INPUT_BRIEF_CHARS` and `MAX_TOOL_RESULT_BRIEF_CHARS` are duplicated with equal values. Two
// runtimes' renderers may legitimately diverge, so unifying them is a decision rather than a cleanup — but
// silent DRIFT between them is not something anyone would notice, so `pi-terminal-frame.test.js` asserts the
// two agree and will fail if one is changed without the other.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { extractPiAssistantText } from "./runtimes.js";

export // ANSI color helpers. xterm.js's WebGL renderer (current dashboard build)
// handles standard 16-color + bright variants and bold/dim. We use a small
// palette consistently so the synthesized terminal feels distinguishable
// from raw assistant text without being a circus.
const ANSI = {
  reset: "\x1b[0m",
  bold: "\x1b[1m",
  dim: "\x1b[2m",
  red: "\x1b[31m",
  green: "\x1b[32m",
  yellow: "\x1b[33m",
  blue: "\x1b[34m",
  magenta: "\x1b[35m",
  cyan: "\x1b[36m",
  brightGreen: "\x1b[92m",
  brightYellow: "\x1b[93m",
  brightCyan: "\x1b[96m",
};

const MAX_PI_ERROR_CAPTURE_CHARS = 65536;

const MAX_TOOL_INPUT_BRIEF_CHARS = 240;

const MAX_TOOL_RESULT_BRIEF_CHARS = 320;

const PI_TRUNCATION_MARKER = "\n...[aify truncated middle output]...\n";

export function boundText(value, limit, { preserveEdges = false } = {}) {
  const text = String(value || "");
  if (text.length <= limit) return text;
  if (!preserveEdges) return text.slice(text.length - limit);
  const payloadLimit = Math.max(0, limit - PI_TRUNCATION_MARKER.length);
  const headLength = Math.ceil(payloadLimit / 2);
  const tailLength = Math.floor(payloadLimit / 2);
  return `${text.slice(0, headLength)}${PI_TRUNCATION_MARKER}${text.slice(text.length - tailLength)}`;
}

export function appendBounded(current, chunk, options = {}) {
  const limit = options.limit || MAX_PI_ERROR_CAPTURE_CHARS;
  return boundText(`${String(current || "")}${String(chunk || "")}`, limit, options);
}

function briefJsonInline(value, limit) {
  if (value === undefined || value === null) return "";
  let text;
  if (typeof value === "string") {
    text = value;
  } else {
    try {
      text = JSON.stringify(value);
    } catch {
      text = String(value);
    }
  }
  text = text.replace(/\s+/g, " ").trim();
  if (!text) return "";
  if (text.length <= limit) return text;
  return `${text.slice(0, Math.max(0, limit - 1))}…`;
}

function formatToolInputBrief(input) {
  return briefJsonInline(input, MAX_TOOL_INPUT_BRIEF_CHARS);
}

function formatToolResultBrief(result) {
  if (result === undefined || result === null) return "";
  if (typeof result === "string") return briefJsonInline(result, MAX_TOOL_RESULT_BRIEF_CHARS);
  if (Array.isArray(result)) {
    const text = result
      .map((part) => {
        if (part && typeof part === "object" && typeof part.text === "string") return part.text;
        return part;
      })
      .join("\n");
    return briefJsonInline(text, MAX_TOOL_RESULT_BRIEF_CHARS);
  }
  if (typeof result === "object") {
    if (typeof result.text === "string") return briefJsonInline(result.text, MAX_TOOL_RESULT_BRIEF_CHARS);
    if (Array.isArray(result.content)) return formatToolResultBrief(result.content);
    return briefJsonInline(result, MAX_TOOL_RESULT_BRIEF_CHARS);
  }
  return briefJsonInline(String(result), MAX_TOOL_RESULT_BRIEF_CHARS);
}

export function colorize(color, text) {
  if (!text) return "";
  return `${color}${text}${ANSI.reset}`;
}

function formatTokenUsage(usage) {
  if (!usage || typeof usage !== "object") return "";
  const input = Number(usage.input_tokens ?? usage.inputTokens ?? usage.prompt_tokens ?? usage.promptTokens);
  const output = Number(usage.output_tokens ?? usage.outputTokens ?? usage.completion_tokens ?? usage.completionTokens);
  const cached = Number(usage.cached_tokens ?? usage.cacheReadInputTokens ?? usage.cache_read_input_tokens);
  const parts = [];
  if (Number.isFinite(input) && input > 0) parts.push(`in=${input}`);
  if (Number.isFinite(output) && output > 0) parts.push(`out=${output}`);
  if (Number.isFinite(cached) && cached > 0) parts.push(`cached=${cached}`);
  return parts.length ? parts.join(" ") : "";
}

export function formatPiEventAsTerminalFrame(event) {
  if (!event || typeof event !== "object") return "";
  const type = String(event.type || "");
  switch (type) {
    case "ready":
      // The banner with model/effort/session is emitted separately by
      // _emitReadyBanner so we can use PiSession context. The "ready" event
      // itself produces no synthesized frame here; the banner replaces it.
      return "";
    case "agent_start":
      return `\r\n${colorize(ANSI.brightCyan + ANSI.bold, "▶ turn started")}\r\n`;
    case "agent_end": {
      const usage = formatTokenUsage(event.usage ?? event.message?.usage ?? event.data?.usage);
      const suffix = usage ? colorize(ANSI.dim, `  (${usage})`) : "";
      return `\r\n${colorize(ANSI.cyan + ANSI.bold, "■ turn ended")}${suffix}\r\n`;
    }
    case "message_end":
    case "turn_end": {
      // Operator-reported 2026-05-22: the synthesized pi terminal stopped
      // at "▶ turn started" + ~1 char of streamed text even though the
      // assistant produced a full multi-paragraph reply. Root cause:
      // OMP streams only a few text_delta events at the head of a turn
      // and then emits the COMPLETE assistant message as
      // message_end/turn_end with `event.message`. The synthesizer
      // formatter didn't handle these event types so the bulk of the
      // reply never made it into the terminal_session.output column.
      // (turn.finalSnapshotText was correctly populating via the same
      // events for the chat reply path — that's why chat worked while
      // Console didn't.)
      const messageText = extractPiAssistantText(event.message);
      if (!messageText) return "";
      // CRLF normalize so the terminal output renders cleanly.
      const normalized = String(messageText).replace(/\r?\n/g, "\r\n");
      return `\r\n${normalized}\r\n`;
    }
    case "error": {
      const msg = String(event.error || event.message || "Pi runtime error");
      return `\r\n${colorize(ANSI.red + ANSI.bold, "✗ error")} ${colorize(ANSI.red, msg)}\r\n`;
    }
    case "tool_execution_start": {
      const name = String(event.tool?.name || event.toolName || event.name || "tool");
      const brief = formatToolInputBrief(event.tool?.input ?? event.input ?? event.arguments);
      const head = colorize(ANSI.yellow, `→ ${name}`);
      const detail = brief ? colorize(ANSI.dim, ` ${brief}`) : "";
      return `\r\n${head}${detail}\r\n`;
    }
    case "tool_execution_end": {
      const name = String(event.tool?.name || event.toolName || event.name || "tool");
      const ok = event.success !== false && !event.error;
      const brief = ok
        ? formatToolResultBrief(event.tool?.result ?? event.result ?? event.output)
        : briefJsonInline(event.error || "", MAX_TOOL_RESULT_BRIEF_CHARS);
      const marker = ok
        ? colorize(ANSI.green, `✓ ${name}`)
        : colorize(ANSI.red, `✗ ${name}`);
      const detail = brief ? colorize(ANSI.dim, ` ${brief}`) : "";
      return `${marker}${detail}\r\n`;
    }
    case "RpcExtensionUIRequest": {
      const req = event.request || event;
      const kind = String(req.kind || req.type || "input");
      const question = String(req.question || req.prompt || req.message || "");
      const options = Array.isArray(req.options) ? req.options.join(" | ") : "";
      const detail = options ? colorize(ANSI.dim, ` (${options})`) : "";
      return `\r\n${colorize(ANSI.magenta + ANSI.bold, `? ${kind}`)} ${question}${detail}\r\n`;
    }
    case "message_update": {
      const inner = event.assistantMessageEvent || event.messageEvent || event.message || {};
      // Operator-reported 2026-05-22: pi synthesized terminal stops at
      // "▶ turn started" + one delta. The first delta arrives in the
      // expected shape (assistantMessageEvent.text_delta), subsequent
      // ones evidently use a different envelope — turn.finalText
      // accumulates correctly so the assistant DOES produce text;
      // formatter just doesn't recognize the later shapes. Defensive
      // fallback covers the variants we've seen in the wild:
      //   - inner.type === "text_delta" + inner.delta (canonical)
      //   - inner.type === "text_end" terminator
      //   - inner.delta directly (no .type field on inner)
      //   - inner.text or inner.content for batch text
      //   - top-level event.delta (sometimes OMP flattens)
      if (inner.type === "text_delta") return String(inner.delta || "");
      if (inner.type === "text_end") return "\r\n";
      if (typeof inner.delta === "string" && inner.delta) return inner.delta;
      if (typeof inner.text === "string" && inner.text) return inner.text;
      if (typeof inner.content === "string" && inner.content) return inner.content;
      if (typeof event.delta === "string" && event.delta) return event.delta;
      return "";
    }
    // Additional top-level shapes that some OMP versions emit directly
    // instead of wrapping in message_update.
    case "text_delta":
    case "agent_message_delta":
    case "agent.message.delta":
    case "message_delta":
    case "delta": {
      const delta = event.delta ?? event.text ?? event.content;
      return typeof delta === "string" ? delta : "";
    }
    case "usage":
    case "token_usage": {
      const usage = formatTokenUsage(event.usage ?? event.data ?? event);
      return usage ? `${colorize(ANSI.dim, `  ${usage}`)}\r\n` : "";
    }
    default:
      return "";
  }
}
