// What this bridge says about an agent when it beats.
//
// Three functions that build and post the agent heartbeat: the fields every beat carries, the variant that
// describes a turn in progress, and the turn-busy report itself. v0.5.4 layer 0 of the server.js
// decomposition, and the prerequisite for extracting `comms_register` — `reportTurnBusy` is read by the
// parked `runDispatchLoop`, so it could not travel with the registration group and the new module could not
// import upward to reach it.
//
// THE HEARTBEAT IS HOW THE SERVICE KNOWS AN AGENT IS ALIVE AND WHAT IT IS DOING, so the fields are not
// bookkeeping. `bridgeId` is what attributes the beat to this process rather than the one it superseded;
// `machineId` and `terminalId` are how a beat is matched to a live worker. A beat missing its bridge id is
// not a beat with less detail — it is a beat the service cannot attribute, which reads as a bridge that has
// gone quiet.
//
// NAME COLLISION, DELIBERATELY NOT RESOLVED. `hermes-run-reporting.mjs` also exports a `reportTurnBusy`, and
// it is a DIFFERENT function: `reportTurnBusy(httpCall, agentId, { busy, runId })` there against
// `reportTurnBusy(agentId, state, { busy, runId, runtime })` here. Two runtimes, two heartbeat shapes, one
// name. Renaming either is a behavioural edit to a call site rather than a relocation, so both keep their
// names and this paragraph exists so the next person importing one of them checks which.
//
// `MACHINE_ID` is bound here by calling the one `defaultMachineId()` in `runtimes.js`, the way
// `claude-channel.js`, `hermes-channel.js`, `hermes-env.mjs`, `hermes-managed-host.js` and
// `agent-summary.mjs` each do. A repeated derivation of a pure function, not a second owner.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { httpCall } from "./aify-service-endpoint.mjs";
import { BRIDGE_INSTANCE_ID } from "./bridge-instance.mjs";
import { cleanEnvPlaceholder } from "./launch-identity.mjs";
import { defaultMachineId } from "./runtimes.js";
import { activeTurnHeartbeatPayload, agentHeartbeatPayload } from "./turn-busy.js";

const MACHINE_ID = defaultMachineId();
export function baseAgentHeartbeatFields(state = {}) {
  return {
    bridgeId: BRIDGE_INSTANCE_ID,
    machineId: state?.info?.machineId || MACHINE_ID,
      terminalId: cleanEnvPlaceholder(process.env.AIFY_TERMINAL_ID || state?.info?.terminalId || ""),
  };
}

export function currentTurnHeartbeatFields(state = {}, activeRun = null) {
  const base = baseAgentHeartbeatFields(state);
  if (!activeRun) return agentHeartbeatPayload(base);
  return activeTurnHeartbeatPayload({
    ...base,
    activeRun,
  });
}

export async function reportTurnBusy(agentId, state = {}, { busy, runId = "", runtime = "" } = {}) {
  return httpCall(
    "POST",
    `/agents/${encodeURIComponent(agentId)}/heartbeat`,
    agentHeartbeatPayload({
      ...baseAgentHeartbeatFields(state),
      turnBusy: !!busy,
      turnRunId: runId,
      turnRuntime: runtime,
    }),
  );
}
