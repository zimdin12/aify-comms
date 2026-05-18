import { normalizeRuntime, runtimeLaunchAvailability } from "./runtimes.js";

export const ENVIRONMENT_RUNTIME_IDS = Object.freeze(["codex", "claude-code", "hermes", "opencode", "pi"]);

export function runtimeCapability(runtime, { availabilityFor = runtimeLaunchAvailability } = {}) {
  const normalized = normalizeRuntime(runtime);
  const availability = availabilityFor(normalized);
  return {
    runtime: normalized,
    modes: ["managed-warm"],
    available: !!availability.available,
    unavailableReason: availability.available ? "" : String(availability.message || "runtime launcher unavailable"),
    capabilities: {
      persistent: true,
      nativeResume: normalized === "codex" || normalized === "hermes" || normalized === "opencode" || normalized === "pi",
      bridgeResume: true,
      cliAttach: false,
      interrupt: true,
      streaming: true,
      tokenTelemetry: false,
      costTelemetry: false,
      contextReset: true,
    },
  };
}

export function advertisedEnvironmentRuntimes(options = {}) {
  return ENVIRONMENT_RUNTIME_IDS.map((runtime) => runtimeCapability(runtime, options));
}

export function advertisedTerminalRuntimes(options = {}) {
  return advertisedEnvironmentRuntimes(options)
    .filter((runtime) => runtime.available)
    .map((runtime) => runtime.runtime);
}
