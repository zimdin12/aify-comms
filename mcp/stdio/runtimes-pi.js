// runtimes-pi.js — Pi (Oh My Pi) runtime helpers: assistant-text/session
// extraction, model normalization, launcher resolution, failure detection.
// Extracted verbatim from runtimes.js (task #123). runtimes.js re-exports
// the public surface.
import { resolveExecutable, inspectShebang, bashShebangFallback } from "./runtimes-exec.js";

export function extractPiAssistantText(value) {
  const messages = Array.isArray(value) ? value : [value];
  const chunks = [];
  for (const message of messages) {
    if (!message || String(message.role || "").toLowerCase() !== "assistant") continue;
    const content = message.content;
    if (typeof content === "string") {
      chunks.push(content);
      continue;
    }
    if (!Array.isArray(content)) continue;
    for (const part of content) {
      if (!part || typeof part !== "object") continue;
      const type = String(part.type || "").toLowerCase();
      if (type === "text" && typeof part.text === "string") chunks.push(part.text);
    }
  }
  return chunks.join("\n").trim();
}

export function extractPiSessionState(value) {
  const source = value && typeof value === "object" ? value : {};
  const data = source.data && typeof source.data === "object" ? source.data : {};
  const session = data.session && typeof data.session === "object"
    ? data.session
    : (source.session && typeof source.session === "object" ? source.session : {});
  const sessionId = String(
    data.sessionId ||
    data.sessionID ||
    source.sessionId ||
    source.sessionID ||
    session.sessionId ||
    session.sessionID ||
    session.id ||
    "",
  ).trim();
  const sessionFile = String(
    data.sessionFile ||
    data.sessionPath ||
    source.sessionFile ||
    source.sessionPath ||
    session.file ||
    session.path ||
    "",
  ).trim();
  return { sessionId, sessionFile };
}

const PI_MODEL_PLACEHOLDER_VALUES = new Set(["default", "unknown", "auto"]);
export function normalizePiModelOverride(value) {
  const text = String(value || "").trim();
  return PI_MODEL_PLACEHOLDER_VALUES.has(text.toLowerCase()) ? "" : text;
}

export function detectPiRuntimeFailure(value) {
  const message = String(value?.message || value || "").replace(/\s+/g, " ").trim();
  const lower = message.toLowerCase();
  if (!lower) return { shouldHeal: false, authFailure: false, fatalRuntime: false, missingSession: false, healReason: null, message };
  const fatalRuntime =
    /fatal error/.test(lower) ||
    /javascript heap out of memory/.test(lower) ||
    /allocation failed/.test(lower) ||
    /\bepipe\b/.test(lower);
  if (fatalRuntime) {
    return { shouldHeal: false, authFailure: false, fatalRuntime: true, missingSession: false, healReason: null, message };
  }
  const authFailure =
    /no api key/.test(lower) ||
    /api key (?:not found|missing|required)/.test(lower) ||
    /not authenticated|authentication (?:failed|required)|unauthori[sz]ed|\b401\b/.test(lower) ||
    ((/amazon-bedrock|bedrock/.test(lower)) && /login|auth|credential|api key/.test(lower));
  if (authFailure) {
    return { shouldHeal: false, authFailure: true, fatalRuntime: false, missingSession: false, healReason: null, message };
  }
  const missingSession =
    /session\s+["']?[^"'\s]+["']?\s+(?:not found|does not exist|missing)/i.test(message) ||
    /no such session/i.test(message);
  if (missingSession) {
    return { shouldHeal: true, authFailure: false, fatalRuntime: false, missingSession: true, healReason: "missing_session", message };
  }
  const projectMismatch =
    /session\s+["']?[^"'\s]+["']?\s+is in another project/i.test(message);
  if (projectMismatch) {
    return { shouldHeal: true, authFailure: false, fatalRuntime: false, missingSession: true, healReason: "project_mismatch", message };
  }
  return { shouldHeal: false, authFailure: false, fatalRuntime: false, missingSession: false, healReason: null, message };
}

export function defaultPiCommand() {
  const configured = String(process.env.AIFY_PI_COMMAND || process.env.PI_COMMAND || "").trim();
  if (process.platform === "win32") {
    return { command: configured || "omp", args: [] };
  }
  const target = configured || "omp";
  const resolved = resolveExecutable(target);
  if (resolved) {
    const shebang = inspectShebang(resolved);
    if (shebang && !shebang.valid) {
      return bashShebangFallback(resolved);
    }
    return { command: resolved, args: [] };
  }
  return { command: target, args: [] };
}
