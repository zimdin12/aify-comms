// Reconciling this environment's MANAGED agents against the service's snapshot. Extracted from
// server.js in v0.5.4.
//
// The loop stays in server.js — busy flag, gate and catch/finally untouched; the body moved
// byte-identical, dedented by two.
//
// What it decides is which managed agents this bridge still OWNS. Getting that wrong in one direction
// leaves an agent hosted by nobody and its work queued forever; in the other, two bridges both believe
// they own it and both claim its runs. The workspace check is here for the same reason it is in the
// spawn and terminal passes: an agent may only be adopted into a root this environment advertised.

import { httpCall } from "./aify-service-endpoint.mjs";
import { REMOTE_AGENT_STATE } from "./bridge-agent-state.mjs";
import { BRIDGE_INSTANCE_ID } from "./bridge-instance.mjs";
import { workspaceWithinRoots } from "./environment-identity.mjs";
import { managedAgentNeedsDispatchHosting, reconcileManagedStateWithSnapshot } from "./managed-teardown-ownership.js";
import { DEFAULT_CWD } from "./registration-inputs.mjs";
import { normalizeRuntime } from "./runtimes.js";
import { normalizeLaunchMode, normalizeSessionMode } from "./session-mode.mjs";
import { isActiveManagedSessionStatus } from "./session-predicates.mjs";
import { IS_ENVIRONMENT_BRIDGE } from "./launch-identity.mjs";
import { IS_REMOTE } from "./aify-service-endpoint.mjs";
import { shouldSkipLoop } from "./loop-gate.mjs";

export async function syncManagedEnvironmentAgentsPass({
  MACHINE_ID,
  effectiveEnvironmentPayload,
  ensureDispatchLoop,
}) {
  const environment = effectiveEnvironmentPayload();
  const [agentsRes, sessionsRes] = await Promise.all([
    httpCall("GET", "/agents"),
    httpCall("GET", `/sessions?environmentId=${encodeURIComponent(environment.id)}&limit=500`),
  ]);
  // A managed agent can be taken over by an operator as resident while this
  // environment bridge remains alive. Drop that now-stale cached managed row
  // as soon as a successful full snapshot proves the mode changed. Without
  // this reconciliation, graceful shutdown can target the resident's
  // identical hermes-managed-host delivery loop and kill its gateway.
  reconcileManagedStateWithSnapshot(REMOTE_AGENT_STATE, agentsRes.agents || {});
  const availableRuntimes = new Set((environment.runtimes || []).filter((item) => item?.available !== false).map((item) => normalizeRuntime(item.runtime)));
  const activeSessionsByAgent = new Map();
  for (const session of sessionsRes.sessions || []) {
    if (!session?.agentId || !isActiveManagedSessionStatus(session.status)) continue;
    if (!activeSessionsByAgent.has(session.agentId)) activeSessionsByAgent.set(session.agentId, session);
  }

  for (const [agentId, managedInfo] of Object.entries(agentsRes.agents || {})) {
    if (normalizeSessionMode(managedInfo.sessionMode) !== "managed") continue;
    if (normalizeLaunchMode(managedInfo.launchMode) === "none") continue;
    const capabilities = managedInfo.capabilities || [];
    if (capabilities.length && !capabilities.includes("managed-run")) continue;

    const session = activeSessionsByAgent.get(agentId);
    const runtimeState = managedInfo.runtimeState || {};
    const belongsToEnvironment =
      session ||
      String(runtimeState.environmentId || "") === environment.id;
    if (!belongsToEnvironment) continue;

    // `available` means this environment can cold-start the agent, not that a
    // worker exists to host. The spawn loop owns that wake path. Adopting every
    // historical available agent here made the 3s dispatch loop GET + heartbeat
    // each one forever. An active session remains authoritative even if its
    // derived status is briefly stale during bridge handover; runSpawnLoop adds
    // newly spawned workers to REMOTE_AGENT_STATE itself.
    if (!session && !managedAgentNeedsDispatchHosting(managedInfo)) continue;

    const runtime = normalizeRuntime((session?.runtime || managedInfo.runtime || "generic"));
    if (!availableRuntimes.has(runtime)) continue;
    const workspace = session?.workspace || managedInfo.cwd || DEFAULT_CWD;
    if (!workspaceWithinRoots(workspace, environment.cwdRoots)) continue;

    const nextRuntimeState = {
      ...runtimeState,
      bridgeInstanceId: BRIDGE_INSTANCE_ID,
      environmentId: environment.id,
      mode: session?.mode || runtimeState.mode || "managed-warm",
    };
    if (session?.spawnRequestId) nextRuntimeState.spawnRequestId = session.spawnRequestId;
    try {
      await httpCall("PATCH", `/agents/${encodeURIComponent(agentId)}/runtime-state`, {
        runtimeState: nextRuntimeState,
      });
    } catch {
      // Best effort; the claim guard also checks the current environment bridge.
    }

    REMOTE_AGENT_STATE.set(agentId, {
      info: {
        agentId,
        role: managedInfo.role || "coder",
        name: managedInfo.name || agentId,
        cwd: workspace,
        model: managedInfo.model || "",
        instructions: managedInfo.instructions || "",
        runtime,
        machineId: managedInfo.machineId || environment.machineId || MACHINE_ID,
        launchMode: "managed",
        sessionMode: "managed",
        sessionHandle: session?.sessionHandle || managedInfo.sessionHandle || "",
        managedBy: managedInfo.managedBy || "dashboard",
        capabilities,
        runtimeConfig: managedInfo.runtimeConfig || {},
        runtimeState: nextRuntimeState,
      },
    });
  }
  if (REMOTE_AGENT_STATE.size) ensureDispatchLoop();
}

// THE LOOP SHELL LIVES HERE NOW, with the busy flag it owns. Its gate, its try/catch/finally and
// its body are byte-identical to what left server.js; the only change is that `shutdownStarted`
// arrives as a parameter, because the flag it reads is set by the shutdown chain server.js owns
// and must be read AFRESH on every tick — a value captured at import would be permanently false.
//
// The TIMER stays in server.js: `ensure*Loop` arms it and `cleanupOnExit` clears it, so it has two
// readers and one of them is the shutdown chain.
let managedEnvironmentSyncBusy = false;
export async function syncManagedEnvironmentAgents({
  MACHINE_ID,
  effectiveEnvironmentPayload,
  ensureDispatchLoop,
  shutdownStarted,
}) {
  if (shouldSkipLoop({ eligible: IS_REMOTE && IS_ENVIRONMENT_BRIDGE, alreadyActive: managedEnvironmentSyncBusy, shuttingDown: shutdownStarted })) return;
  managedEnvironmentSyncBusy = true;
  try {
    await syncManagedEnvironmentAgentsPass({
      MACHINE_ID,
      effectiveEnvironmentPayload,
      ensureDispatchLoop,
    });
  } catch (error) {
    if (error?.status !== 404) {
      console.error("[aify] managed environment sync failed:", error?.message || error);
    }
  } finally {
    managedEnvironmentSyncBusy = false;
  }
}
