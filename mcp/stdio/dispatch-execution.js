import { normalizeRuntime } from "./runtimes.js";
// MUST stay in sync with service-side _NATIVE_MANAGED_RUNTIMES in
// service/routers/api_v2.py. When a runtime is added here, the bridge's
// dispatch loop claims its managed runs via `/dispatch/claim
// executionModes=['managed']` and the native controller handles
// delivery. When a runtime is added on the service side but NOT here,
// `supportedExecutionModes` returns [] for it, the dispatch loop at
// server.js:1849 hits `if (!executionModes.length) continue;` and
// silently skips claiming — operator-reported 2026-05-22 as "hermes
// dispatches sit queued forever after the routing fix landed."
export const NATIVE_MANAGED_RUNTIMES = new Set(["codex", "opencode", "pi", "hermes"]);

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
