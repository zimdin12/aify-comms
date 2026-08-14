// Which managed agents in THIS environment are candidates for teardown, and whose bridge owns them.
//
// Extracted from server.js in v0.5.4. It reaps nothing — it is the READ the reaping decision is made
// from, and that is why it is worth isolating: the filter chain below decides membership, and an agent
// that wrongly passes it belongs to ANOTHER environment. Nothing tested any of it, because server.js is
// imported by no test.
//
// `effectiveEnvironmentPayload` is INJECTED rather than moved. It reads `remoteEffectiveCwdRoots`, a
// mutable whose single writer is `heartbeatEnvironment` — a function that stays in server.js — so the
// state stays with its writer and this module asks for the payload instead of owning it. Same shape as
// `registerRegistrationTool`, which is handed `ensureDispatchLoop` for the same reason. It is passed as a
// FUNCTION, not a value, so each call still reads the roots as they are at that moment.

import { BRIDGE_INSTANCE_ID } from "./bridge-instance.mjs";
import { DEFAULT_CWD } from "./registration-inputs.mjs";
import { bridgeOwnerIsLive } from "./reap-managed-survivors.js";
import { httpCall } from "./aify-service-endpoint.mjs";
import { normalizeSessionMode } from "./session-mode.mjs";
import { workspaceWithinRoots } from "./environment-identity.mjs";

export function createManagedOwnershipReader({ effectiveEnvironmentPayload }) {
  async function fetchManagedOwnershipForEnv() {
    const environment = effectiveEnvironmentPayload();
    const [agentsRes, sessionsRes, environmentsRes] = await Promise.all([
      httpCall("GET", "/agents"),
      httpCall("GET", `/sessions?environmentId=${encodeURIComponent(environment.id)}&limit=500`),
      httpCall("GET", "/environments"),
    ]);
    const sessionByAgent = new Map();
    for (const session of sessionsRes?.sessions || []) {
      if (session?.agentId && !sessionByAgent.has(session.agentId)) sessionByAgent.set(session.agentId, session);
    }
    const environments = Array.isArray(environmentsRes?.environments) ? environmentsRes.environments : [];
    const records = [];
    for (const [agentId, info] of Object.entries(agentsRes?.agents || {})) {
      if (normalizeSessionMode(info.sessionMode) !== "managed") continue;
      const runtimeState = info.runtimeState || {};
      const session = sessionByAgent.get(agentId);
      const belongsToEnvironment =
        session || String(runtimeState.environmentId || "") === environment.id;
      if (!belongsToEnvironment) continue;
      const workspace = session?.workspace || info.cwd || DEFAULT_CWD;
      if (!workspaceWithinRoots(workspace, environment.cwdRoots)) continue;
      const owningBridgeId = String(runtimeState.bridgeInstanceId || "").trim();
      records.push({
        agentId,
        owningBridgeId,
        ownerLive: bridgeOwnerIsLive(owningBridgeId, {
          environments,
          selfBridgeId: BRIDGE_INSTANCE_ID,
        }),
      });
    }
    return records;
  }

  return fetchManagedOwnershipForEnv;
}
