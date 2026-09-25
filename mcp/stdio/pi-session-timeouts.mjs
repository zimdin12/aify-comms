// pi's timeout policy: which config key, env var and default each of its timeouts reads.
//
// The reading itself (config, else env, else default) and `createDeferred` are shared with the other
// runtime session modules in `session-timing.mjs`; the keys stay here so pi cannot pick up another
// runtime's timeout.

import { getRuntimeConfig } from "./runtimes.js";
import { positiveMsFrom } from "./session-timing.mjs";

const DEFAULT_IDLE_TIMEOUT_MS = 24 * 60 * 60 * 1000;
const STARTUP_TIMEOUT_DEFAULT_MS = 45000;


export function idleTimeoutFor(agentInfo) {
  return positiveMsFrom(
    getRuntimeConfig(agentInfo).piIdleTimeoutMs, process.env.AIFY_PI_IDLE_TIMEOUT_MS, DEFAULT_IDLE_TIMEOUT_MS,
  );
}

export function startupTimeoutFor(agentInfo) {
  return positiveMsFrom(
    getRuntimeConfig(agentInfo).startupTimeoutMs, process.env.AIFY_PI_STARTUP_TIMEOUT_MS, STARTUP_TIMEOUT_DEFAULT_MS,
  );
}

export function timeoutFor(agentInfo) {
  const cfg = getRuntimeConfig(agentInfo);
  const value = Number(cfg.timeoutMs);
  return Number.isFinite(value) && value > 0 ? value : 12 * 60 * 60 * 1000;
}
