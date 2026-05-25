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
  const isWrapperBacked = managedViaWrapperRuntimes && (
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
  // Plan 5 (2026-05-25) symmetric channel-claim: when the agent is recorded
  // as sessionMode='managed' AND the runtime is wrapper-backed
  // (managed_via_wrapper includes it), the main bridge claims execution
  // mode 'channel'. This mirrors the server-side route at api_v2.py:1047
  // which sets execution_mode='channel' for wrapper-backed managed
  // dispatches. Without this branch, the main bridge requests []
  // (the legacy 'managed' push above is gated off by !isWrapperBacked),
  // the wrapper child only polls for its own AIFY_AGENT_ID, and runs
  // targeting any other managed wrapper-backed agent sit queued forever
  // (observed 2026-05-25 — graph-senior-dev codex managed, pi managed,
  // hermes managed). Scope is restricted to {codex,hermes,pi} to match
  // _CHANNEL_MANAGED_RUNTIMES on the service side; opencode is excluded
  // (operator policy + opencode adapter declares preferred_delivery_mode
  // != "managed-via-wrapper").
  if (
    sessionMode === "managed" &&
    isWrapperBacked &&
    (runtime === "codex" || runtime === "hermes" || runtime === "pi")
  ) {
    modes.push("channel");
  }
  if (sessionMode === "resident" && capabilities.includes("resident-run")) {
    if (runtime === "codex" || runtime === "hermes" || runtime === "opencode" || runtime === "pi") {
      modes.push("resident");
    }
  }
  return modes;
}
