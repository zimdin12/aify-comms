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

// Wrapper-child claim augmentation (server.js dispatch loop). When a bridge IS
// the wrapper PTY child for a managed-via-wrapper agent (AIFY_MANAGED_VIA_WRAPPER=1),
// it adds channel/resident to its claim modes so it — not the environment
// bridge — owns delivery. This is correct for CODEX (the wrapper child's
// in-process bridge IS the delivery surface).
//
// It is NOT correct for managed HERMES under the visible-TUI model: the
// wrapper child is the thin `hermes --tui` (a WS client that only renders); the
// per-agent `hermes-managed-host.js run <agent>` delivery loop owns channel/
// resident delivery and claims those runs as bridgeKind="channel-sidecar". If
// the hermes wrapper child ALSO advertised channel/resident, the two claimants
// race on the same run; when the wrapper child wins, the run flows through the
// HermesController's ChannelDelegatedController (a leftover api_server-era no-op
// that resolves "delegated"), so server.js marks the run completed and the
// strict-reply/auto-mirror path fabricates a summary instead of the real agent
// reply. So: hermes wrapper children must NOT claim channel/resident.
//
// Returns the augmented mode set (deduped). Pure + unit-testable.
export function wrapperChildExecutionModes(baseModes, { runtime, isWrapperChild } = {}) {
  const modes = Array.isArray(baseModes) ? [...baseModes] : [];
  if (!isWrapperChild) return modes;
  const rt = normalizeRuntime(runtime || "generic");
  // Managed hermes delivery is owned by the hermes-managed-host.js loop, never
  // by the wrapper child. Do not let the wrapper child race it.
  if (rt === "hermes") return modes;
  for (const mode of ["channel", "resident"]) {
    if (!modes.includes(mode)) modes.push(mode);
  }
  return modes;
}
