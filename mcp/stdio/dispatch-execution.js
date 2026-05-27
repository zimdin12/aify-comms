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

export function supportedExecutionModes(info = {}, options = {}) {
  const sessionMode = String(info.sessionMode || "").trim().toLowerCase();
  const runtime = normalizeRuntime(info.runtime || "generic");
  const capabilities = Array.isArray(info.capabilities) ? info.capabilities : [];
  // Unified-backing refactor 2026-05-24: when the runtime is wrapper-backed
  // (operator flipped managed_via_wrapper for this runtime), the main bridge
  // must NOT claim managed dispatches — the wrapper's child bridge claims
  // instead. Without this gate, both bridges race to claim the same run.
  const managedViaWrapperRuntimes = (options && options.managedViaWrapperRuntimes) || null;
  const wrapperEligible = runtime === "codex" || runtime === "hermes";
  const isWrapperBacked = wrapperEligible && managedViaWrapperRuntimes && (
    typeof managedViaWrapperRuntimes.has === "function"
      ? managedViaWrapperRuntimes.has(runtime)
      : Array.isArray(managedViaWrapperRuntimes) && managedViaWrapperRuntimes.includes(runtime)
  );
  const modes = [];
  if (
    sessionMode === "managed" &&
    (capabilities.includes("native-managed-run") || NATIVE_MANAGED_RUNTIMES.has(runtime)) &&
    !isWrapperBacked
  ) {
    modes.push("managed");
  }
  // Wrapper-backed managed Codex/Hermes runs are persisted as
  // execution_mode='channel' by the service, but the environment bridge must
  // not claim them. The wrapper PTY's child bridge runs with
  // AIFY_MANAGED_VIA_WRAPPER=1 and server.js adds channel/resident claim modes
  // for that child only. Letting the environment bridge advertise 'channel'
  // races the child bridge and can drive stale runtimeConfig instead of the
  // visible wrapper session.
  if (sessionMode === "resident" && capabilities.includes("resident-run")) {
    if (runtime === "codex" || runtime === "hermes") {
      modes.push("resident");
    }
  }
  return modes;
}
