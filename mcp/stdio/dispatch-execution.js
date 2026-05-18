import { normalizeRuntime } from "./runtimes.js";

export function supportedExecutionModes(info = {}) {
  const sessionMode = String(info.sessionMode || "").trim().toLowerCase();
  const runtime = normalizeRuntime(info.runtime || "generic");
  const capabilities = Array.isArray(info.capabilities) ? info.capabilities : [];
  const modes = [];
  if (sessionMode === "managed" && capabilities.includes("native-managed-run")) {
    modes.push("managed");
  }
  if (sessionMode === "resident" && capabilities.includes("resident-run")) {
    if (runtime === "codex" || runtime === "hermes" || runtime === "opencode" || runtime === "pi") {
      modes.push("resident");
    }
  }
  return modes;
}
