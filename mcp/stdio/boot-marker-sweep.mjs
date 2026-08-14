// Clearing tombstoned agent markers at boot.
//
// A removed agent can leave marker files behind in the temp dir. This runs once per environment-bridge
// boot, asks the service which agents still exist, and has `sweepTombstonedMarkers` delete markers for the
// rest.
//
// THE FAIL-SAFE IS THE WHOLE FUNCTION. The sweep deletes markers for every agent NOT in the list it is
// given, so the list being wrong is destructive in one direction only: an incomplete keyset sweeps markers
// belonging to agents that are perfectly alive. So a failed `/agents` query returns early and sweeps
// nothing — except a 404, which genuinely means "no agents yet" and makes an empty keyset the correct
// answer rather than a missing one. Those two failure shapes look identical in a log and mean opposite
// things.
//
// NOT MERGED INTO `reap-managed-survivors.js`, which owns `sweepTombstonedMarkers`. That module is process
// and filesystem primitives with SEVEN imports and no service dependency at all — zero `httpCall`, zero
// `fetch`. This function's first act is a service query. Joining an existing owner is the rule here, but
// not when it would hand a deliberately offline module a network dependency.
//
// Extracted from server.js in v0.5.4; byte-identical to the declaration that stood there, the only
// substitution being the added `export `.


import os from "node:os";
import { IS_REMOTE, httpCall } from "./aify-service-endpoint.mjs";
import { IS_ENVIRONMENT_BRIDGE } from "./launch-identity.mjs";
import { sweepTombstonedMarkers } from "./reap-managed-survivors.js";

export async function runBootTombstonedMarkerSweep() {
  if (!IS_REMOTE || !IS_ENVIRONMENT_BRIDGE) return;
  let knownAgentIds = null;
  try {
    const agentsRes = await httpCall("GET", "/agents");
    knownAgentIds = Object.keys(agentsRes?.agents || {});
  } catch (error) {
    // Unknown keyset → fail-safe (sweep nothing). 404 is just "no agents yet".
    if (error?.status !== 404) {
      console.error("[aify] boot tombstoned-marker sweep: /agents query failed — sweeping nothing (fail-safe):", error?.message || error);
      return;
    }
    knownAgentIds = [];
  }
  try {
    const result = sweepTombstonedMarkers({ knownAgentIds, tempDir: os.tmpdir() });
    if (result?.skipped) return;
    const n = result?.swept?.length || 0;
    if (n) {
      console.error(`[aify] boot tombstoned-marker sweep: cleared markers for ${n} removed agent(s)`);
    }
    if (result?.errors?.length) {
      console.error(`[aify] boot tombstoned-marker sweep had ${result.errors.length} error(s):`, JSON.stringify(result.errors));
    }
  } catch (error) {
    console.error("[aify] boot tombstoned-marker sweep failed:", error?.message || error);
  }
}
