import { normalizeRuntime } from "./runtimes.js";
const NATIVE_MANAGED_RUNTIMES = new Set(["codex", "opencode", "pi"]);

export function supportedExecutionModes(info = {}) {
  const sessionMode = String(info.sessionMode || "").trim().toLowerCase();
  const runtime = normalizeRuntime(info.runtime || "generic");
  const capabilities = Array.isArray(info.capabilities) ? info.capabilities : [];
  const modes = [];
  if (
    sessionMode === "managed" &&
    (capabilities.includes("native-managed-run") || NATIVE_MANAGED_RUNTIMES.has(runtime))
  ) {
    modes.push("managed");
  }
  if (sessionMode === "resident" && capabilities.includes("resident-run")) {
    if (runtime === "codex" || runtime === "hermes" || runtime === "opencode" || runtime === "pi") {
      modes.push("resident");
    }
  }
  return modes;
}
