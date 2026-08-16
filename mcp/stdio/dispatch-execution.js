import { normalizeRuntime } from "./runtimes.js";
// MUST stay in sync with service-side _NATIVE_MANAGED_RUNTIMES in
// service/api_core/runtime.py (it was in service/routers/api_v2.py until the v0.5 domain
// extraction; that file is now 53 lines of include_router calls). When a runtime is added here, the bridge's
// dispatch loop claims its managed runs via `/dispatch/claim
// executionModes=['managed']` and the native controller handles
// delivery. When a runtime is added on the service side but NOT here,
// `supportedExecutionModes` returns [] for it, the dispatch loop at
// server.js hits `if (!executionModes.length) continue;` and
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
    // CODEX resident: the resident wrapper's in-process bridge IS the delivery
    // surface, so this main bridge claims 'resident' directly.
    //
    // HERMES resident is NOT claimed here (2026-06-03 fabricated-reply fix):
    // resident hermes delivery is owned by the per-agent
    // `hermes-managed-host.js run <agent>` loop (bridgeKind="channel-sidecar"),
    // exactly like managed hermes. If the resident MAIN bridge (bridge_kind=
    // 'resident') claimed the run, it would route through launchRuntimeRun ->
    // HermesController -> ChannelDelegatedController (a leftover no-op that
    // resolves status:"delegated" with the summary "channel/resident dispatch
    // delegated to hermes-managed-host.js delivery loop"); server.js then marks
    // the run completed and the auto-mirror path posts THAT summary as the
    // agent's reply — a fabricated reply, no real turn, nothing in the TUI.
    // The wrapper-child exclusion (wrapperChildExecutionModes) only covered the
    // AIFY_MANAGED_VIA_WRAPPER=1 child, never this resident main bridge. So
    // hermes is excluded here too; its channel-sidecar loop is the sole claimer
    // of channel/resident hermes runs and delivers via the real gateway submit.
    if (runtime === "codex") {
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
// It is ALSO NOT correct for managed CLAUDE (operator-reported 2026-05-31,
// run_1780205398406 sc-manager→sc-claude FAILED): claude's channel/resident
// delivery is owned by the `claude-channel.js` CHANNEL-SIDECAR (loaded via
// --dangerously-load-development-channels, claims as bridgeKind="channel-sidecar"
// and delivers via MCP notification). Claude's `aify-comms` MCP — running in the
// same managed PTY with a terminalId — registers as a managed-wrapper-child and
// exists for the agent's comms_send REPLIES, not delivery. If it ALSO advertised
// channel/resident it races the channel-sidecar; when it wins, the run flows to
// the CLAUDE controller's removed `claude -p` path and FAILS ("Claude Code
// managed Messenger no longer uses claude -p…"). So: claude wrapper children must
// NOT claim channel/resident either — the channel-sidecar owns it.
//
// Net: only CODEX wrapper children claim channel/resident (codex's in-process
// child IS its delivery surface; it has no separate sidecar). claude + hermes
// have dedicated channel-sidecars and must not race them.
//
// Returns the augmented mode set (deduped). Pure + unit-testable.
export function wrapperChildExecutionModes(baseModes, { runtime, isWrapperChild } = {}) {
  const modes = Array.isArray(baseModes) ? [...baseModes] : [];
  if (!isWrapperChild) return modes;
  const rt = normalizeRuntime(runtime || "generic");
  // Managed hermes + claude delivery is owned by a dedicated channel-sidecar
  // (hermes-managed-host.js loop / claude-channel.js), never by the wrapper
  // child. Do not let the wrapper child race the sidecar.
  if (rt === "hermes" || rt === "claude-code") return modes;
  for (const mode of ["channel", "resident"]) {
    if (!modes.includes(mode)) modes.push(mode);
  }
  return modes;
}
