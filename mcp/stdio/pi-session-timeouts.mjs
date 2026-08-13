// The four timing/promise helpers `PiSession` is built on — pi's, and deliberately ONLY pi's.
//
// EACH OF THESE IS DUPLICATED ACROSS THE FOUR RUNTIME SESSION MODULES, and `tests/deferred-agreement.test.js`
// pins that as INTENTIONAL rather than as debt: `idleTimeoutFor` reads a different config key and a
// different env var per runtime, so unifying the four would silently hand one runtime another's timeout.
// Moving pi's copies into a pi-only module keeps them per-runtime — it changes their address, not their
// number.
//
// `createDeferred`'s `promise.catch(() => {})` is load-bearing and was MISSING here until v0.5.4: these
// modules reject Deferreds on ordinary paths (a session that fails to start, a turn that is cancelled),
// and in Node an unhandled rejection is a process kill under `--unhandled-rejections=strict`. pi was the
// only one of the four without it. Do not "tidy" it away.

import { getRuntimeConfig } from "./runtimes.js";

const DEFAULT_IDLE_TIMEOUT_MS = 24 * 60 * 60 * 1000;
const STARTUP_TIMEOUT_DEFAULT_MS = 45000;


export function createDeferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  // Attach a no-op .catch so a rejection on a Deferred that ends up with
  // no real awaiter doesn't become an unhandled-rejection. Real awaiters
  // sharing `promise` still see their own .catch handlers fire.
  //
  // v0.5.4: pi was the ONLY one of the four session modules missing this.
  // `codex-session.js`, `hermes-session.js` and `hermes-managed-gateway-session.js`
  // all carried it; the guard was added to them and not here.
  promise.catch(() => {});
  return { promise, resolve, reject };
}

export function idleTimeoutFor(agentInfo) {
  const cfg = getRuntimeConfig(agentInfo);
  const fromConfig = Number(cfg.piIdleTimeoutMs);
  if (Number.isFinite(fromConfig) && fromConfig > 0) return fromConfig;
  const fromEnv = Number(process.env.AIFY_PI_IDLE_TIMEOUT_MS);
  if (Number.isFinite(fromEnv) && fromEnv > 0) return fromEnv;
  return DEFAULT_IDLE_TIMEOUT_MS;
}

export function startupTimeoutFor(agentInfo) {
  const cfg = getRuntimeConfig(agentInfo);
  const fromConfig = Number(cfg.startupTimeoutMs);
  if (Number.isFinite(fromConfig) && fromConfig > 0) return fromConfig;
  const fromEnv = Number(process.env.AIFY_PI_STARTUP_TIMEOUT_MS);
  if (Number.isFinite(fromEnv) && fromEnv > 0) return fromEnv;
  return STARTUP_TIMEOUT_DEFAULT_MS;
}

export function timeoutFor(agentInfo) {
  const cfg = getRuntimeConfig(agentInfo);
  const value = Number(cfg.timeoutMs);
  return Number.isFinite(value) && value > 0 ? value : 12 * 60 * 60 * 1000;
}
