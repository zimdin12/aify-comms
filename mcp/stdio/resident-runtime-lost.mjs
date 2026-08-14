// Reporting that a resident runtime has gone, and standing the bridge down when its last agent goes
// with it. Extracted from server.js in v0.5.4.
//
// The `finally` is the whole point and it runs on BOTH paths: whether the report reached the service or
// failed, this bridge stops claiming for that agent. Reporting-then-forgetting only on success would
// leave a bridge holding an agent whose runtime is gone every time the service was briefly unreachable —
// and it would keep claiming that agent's work.
//
// The last-agent exit is deferred by 50ms and unref'd so the report can flush first, and gated on NOT
// being an environment bridge: an env bridge with no agents is idle and expected, a resident bridge with
// none has nothing left to host.
//
// `shutdownWithStatus` and `MACHINE_ID` are injected — server.js owns the shutdown chain, and a module
// that could end the process on its own is a worse thing to own than one handed the ability.

import { httpCall } from "./aify-service-endpoint.mjs";
import { REMOTE_AGENT_STATE, forgetRemoteAgent } from "./bridge-agent-state.mjs";
import { BRIDGE_INSTANCE_ID } from "./bridge-instance.mjs";
import { IS_ENVIRONMENT_BRIDGE } from "./launch-identity.mjs";
import { normalizeRuntime } from "./runtimes.js";

export async function reportResidentRuntimeLost(
  agentId,
  info = {},
  reason = "resident runtime app-server is unreachable",
  { MACHINE_ID, shutdownWithStatus } = {},
) {
  try {
    const result = await httpCall("POST", `/agents/${encodeURIComponent(agentId)}/resident-lost`, {
      bridgeId: BRIDGE_INSTANCE_ID,
      machineId: info.machineId || MACHINE_ID,
      runtime: normalizeRuntime(info.runtime || "generic"),
      reason,
    });
    const transition = result?.transition ? ` (${result.transition})` : "";
    console.error(`[aify] resident runtime lost for "${agentId}"${transition}: ${reason}`);
  } catch (error) {
    console.error(`[aify] failed to report resident runtime loss for "${agentId}": ${error?.message || error}`);
  } finally {
    forgetRemoteAgent(agentId, reason);
    if (!IS_ENVIRONMENT_BRIDGE && REMOTE_AGENT_STATE.size === 0) {
      setTimeout(() => { shutdownWithStatus(0); }, 50).unref();
    }
  }
}
