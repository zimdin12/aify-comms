// Has a resident Codex agent's app-server binding gone away?
//
// The control loop asks this each pass. Answering "yes" costs the agent its binding, so the decision has
// HYSTERESIS: one unreachable probe is not enough, two consecutive ones are. A single transient failure —
// a restart, a busy moment, a 1.2s timeout under load — would otherwise tear down a healthy resident
// agent, and the per-agent counter is what stops that.
//
// The counter is cleared on every success and on every "not applicable" answer, so the two failures have
// to be genuinely consecutive rather than accumulated over an afternoon.
//
// SCOPE IS DELIBERATELY NARROW: resident + codex only. A managed agent's liveness is decided elsewhere,
// and any other runtime has no app-server to probe. Both non-matching cases return false WITHOUT probing,
// which is why the guard runs before anything expensive.
//
// NOT MERGED into `bridge-agent-state.mjs`, though it keeps a per-agent Map like the three there. That
// module's invariant is that `comms_clear` resets its Maps TOGETHER; this counter is not part of that
// reset and adding it would quietly widen the invariant. Nor into `runtimes-rpc.js`, which owns the
// `codexAppServerReachable` probe — that module is JSON-RPC transport clients, not policy.
//
// Extracted from server.js in v0.5.4; byte-identical to the declarations that stood there, the only
// substitution being the added `export `.


// `runtimes.js` re-exports the public surface of runtimes-rpc.js; server.js imports both of these from
// there, so this module follows the same door rather than reaching past it into the transport module.
import { codexAppServerReachable, normalizeRuntime } from "./runtimes.js";
import { normalizeSessionMode } from "./session-mode.mjs";

export const RESIDENT_BINDING_FAILURES = new Map();
export const RESIDENT_BINDING_LOST_AFTER_FAILURES = 2;
export async function residentRuntimeBindingLost(agentId, info = {}) {
  const sessionMode = normalizeSessionMode(info.sessionMode);
  const runtime = normalizeRuntime(info.runtime || "generic");
  if (sessionMode !== "resident" || runtime !== "codex") return false;
  const runtimeConfig = info.runtimeConfig || {};
  const appServerUrl = String(runtimeConfig.appServerUrl || "").trim();
  if (!appServerUrl || !info.sessionHandle) {
    RESIDENT_BINDING_FAILURES.delete(agentId);
    return false;
  }
  const remoteAuthTokenEnv = String(runtimeConfig.remoteAuthTokenEnv || "").trim();
  const token = remoteAuthTokenEnv ? String(process.env[remoteAuthTokenEnv] || "").trim() : "";
  const reachable = await codexAppServerReachable(appServerUrl, { token, timeoutMs: 1200 });
  if (reachable) {
    RESIDENT_BINDING_FAILURES.delete(agentId);
    return false;
  }
  const failures = (RESIDENT_BINDING_FAILURES.get(agentId) || 0) + 1;
  RESIDENT_BINDING_FAILURES.set(agentId, failures);
  console.error(`[aify] resident Codex app-server for "${agentId}" is unreachable (${failures}/${RESIDENT_BINDING_LOST_AFTER_FAILURES}): ${appServerUrl}`);
  return failures >= RESIDENT_BINDING_LOST_AFTER_FAILURES;
}
