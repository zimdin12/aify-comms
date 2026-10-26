// What this bridge says about an agent when it beats.
//
// The fields every beat carries, and the post. The beat never describes a turn: since the 0.9.8 switch the
// turn is aify-env's, and 0.9.9 S3c removed the turn-busy reports comms had ignored since S2a.
//
// THE HEARTBEAT IS HOW THE SERVICE KNOWS AN AGENT IS ALIVE AND WHAT IT IS DOING, so the fields are not
// bookkeeping. `bridgeId` is what attributes the beat to this process rather than the one it superseded;
// `machineId` and `terminalId` are how a beat is matched to a live worker. A beat missing its bridge id is
// not a beat with less detail — it is a beat the service cannot attribute, which reads as a bridge that has
// gone quiet.
//
// `MACHINE_ID` is bound here by calling the one `defaultMachineId()` in `machine-id.mjs`, the way
// `claude-channel.js`, `hermes-channel.js`, `hermes-env.mjs`, `hermes-managed-host.js` and
// `agent-summary.mjs` each do. A repeated derivation of a pure function, not a second owner.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { httpCall } from "./aify-service-endpoint.mjs";
import { BRIDGE_INSTANCE_ID } from "./bridge-instance.mjs";
import { cleanEnvPlaceholder } from "./launch-identity.mjs";
import { defaultMachineId } from "./machine-id.mjs";

const MACHINE_ID = defaultMachineId();

export function agentHeartbeatFields(state = {}) {
  const body = {
    bridgeId: BRIDGE_INSTANCE_ID,
    machineId: String(state?.info?.machineId || MACHINE_ID),
  };
  // Omitted rather than sent empty: an empty id would match no worker.
  const terminal = cleanEnvPlaceholder(process.env.AIFY_TERMINAL_ID || state?.info?.terminalId || "").trim();
  if (terminal) body.terminalId = terminal;
  return body;
}

export async function reportAgentHeartbeat(agentId, state = {}) {
  return httpCall("POST", `/agents/${encodeURIComponent(agentId)}/heartbeat`, agentHeartbeatFields(state));
}
