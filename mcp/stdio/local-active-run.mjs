// Whether this bridge still believes a run is active locally, checked against what the service says.
//
// Two functions. `reconcileLocalActiveRun` asks the service about a run this bridge thinks it is executing
// and drops the local record if the service disagrees; `clearLocalActiveRun` is the drop itself — interrupt
// the controller, forget the run, and report the agent no longer busy. v0.5.4 layer 0 of the server.js
// decomposition, and prerequisite 2 for extracting `comms_register`: both are read by the parked
// `runDispatchLoop`, so they could not travel with the registration group and that group could not import
// upward to reach them.
//
// A TRANSIENT BACKEND FAILURE MUST NOT LOOK LIKE A FORGOTTEN RUN, and that asymmetry is the whole design.
// Only a 404 — the service affirmatively saying the run is gone — is allowed to drop the local record. Any
// other error returns false and keeps it. The source comment says why: forgetting a run that IS executing
// frees the claim loop to take the same work again, so the agent runs it twice. A stale record costs one
// blocked claim cycle; a wrongly-dropped one costs duplicate execution, and the two are not symmetric.
//
// CLEARING IS THREE THINGS AND ALL OF THEM MATTER. The controller is interrupted (best-effort, because the
// point is unblocking the claim loop rather than a clean stop), the run leaves `ACTIVE_RUNS` so nothing
// still believes it is live, and turn-busy is reported false so the agent's status is released. Skipping the
// last would leave an agent reading `working` with no run able to clear it — the symptom the turn-busy
// heartbeat exists to prevent.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { httpCall } from "./aify-service-endpoint.mjs";
import { reportTurnBusy } from "./agent-heartbeat.mjs";
import { ACTIVE_RUNS } from "./bridge-agent-state.mjs";
import { BRIDGE_INSTANCE_ID } from "./bridge-instance.mjs";
import { shouldDropLocalActiveRun } from "./dispatch-state.js";
import { normalizeRuntime } from "./runtimes.js";
export async function clearLocalActiveRun(agentId, state, active, reason) {
  if (!active?.runId) return;
  try {
    active.controller?.interrupt?.(`Local active run cleared (${reason})`);
  } catch {
    // best effort; the important part is unblocking the claim loop
  }
  ACTIVE_RUNS.delete(agentId);
  await reportTurnBusy(agentId, state, {
    busy: false,
    runId: active.runId,
    runtime: active.runtime || normalizeRuntime(state?.info?.runtime || "generic"),
  }).catch(() => {});
}

export async function reconcileLocalActiveRun(agentId, state, active) {
  if (!active?.runId) return false;
  let backendRun = null;
  try {
    const response = await httpCall("GET", `/dispatch/runs/${encodeURIComponent(active.runId)}`);
    backendRun = response?.run || null;
  } catch (error) {
    if (error?.status !== 404) {
      // Transient backend failures must not make us forget an actually running
      // local turn and accidentally claim duplicate work.
      return false;
    }
  }
  const decision = shouldDropLocalActiveRun(active, backendRun, {
    bridgeId: BRIDGE_INSTANCE_ID,
    agentId,
  });
  if (!decision.drop) return false;
  await clearLocalActiveRun(agentId, state, active, decision.reason);
  console.error(`[aify] dropped stale local active run for "${agentId}" (${active.runId}): ${decision.reason}`);
  return true;
}
