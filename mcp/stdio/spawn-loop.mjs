// The spawn-request claim pass, extracted from server.js in v0.5.4.
//
// The LOOP stays in server.js — timer, busy flag, shutdown gate and catch/finally are untouched. Only
// the pass moved, byte-identical, dedented by two.
//
// This is what turns a dashboard spawn request into a running agent. Its riskiest step is the
// WORKSPACE CHECK: a request names a cwd, and `workspaceWithinRoots` is what stops this bridge
// launching a process outside the roots its environment declared. The claim is also reported back
// either way — a claimed request that is never resolved is one the service will hand to nobody else.

import { spawn } from "child_process";

import { httpCall } from "./aify-service-endpoint.mjs";
import { REMOTE_AGENT_STATE } from "./bridge-agent-state.mjs";
import { BRIDGE_INSTANCE_ID } from "./bridge-instance.mjs";
import { noteSpawnClaimFailure, noteSpawnClaimSuccess } from "./claim-failure-tracker.mjs";
import { workspaceWithinRoots } from "./environment-identity.mjs";
import { DEFAULT_CWD } from "./registration-inputs.mjs";
import { defaultCapabilitiesForRuntime, normalizeRuntime } from "./runtimes.js";
import { IS_ENVIRONMENT_BRIDGE } from "./launch-identity.mjs";
import { IS_REMOTE } from "./aify-service-endpoint.mjs";
import { shouldSkipLoop } from "./loop-gate.mjs";

export async function runSpawnPass({
  CLAIM_OPTS,
  CLAIM_WAIT_MS,
  MACHINE_ID,
  effectiveEnvironmentPayload,
  ensureDispatchLoop,
}) {
  const environment = effectiveEnvironmentPayload();
  let claim;
  try {
    claim = await httpCall("POST", "/spawn-requests/claim", {
      environmentId: environment.id,
      bridgeId: BRIDGE_INSTANCE_ID,
      machineId: MACHINE_ID,
      waitMs: CLAIM_WAIT_MS,
    }, CLAIM_OPTS);
  } catch (error) {
    if (error?.status !== 404) {
      noteSpawnClaimFailure(error);
    }
    return;
  }
  noteSpawnClaimSuccess();
  const spawnRequest = claim?.spawnRequest;
  if (!spawnRequest) return;

  const workspace = spawnRequest.workspace || spawnRequest.workspaceRoot || DEFAULT_CWD;
  if (!workspaceWithinRoots(workspace, environment.cwdRoots)) {
    await httpCall("PATCH", `/spawn-requests/${encodeURIComponent(spawnRequest.id)}`, {
      status: "failed",
      bridgeId: BRIDGE_INSTANCE_ID,
      error: `Workspace "${workspace}" is outside this bridge's advertised roots`,
    });
    return;
  }

  await httpCall("PATCH", `/spawn-requests/${encodeURIComponent(spawnRequest.id)}`, {
    status: "starting",
    bridgeId: BRIDGE_INSTANCE_ID,
  });

  const runtime = normalizeRuntime(spawnRequest.runtime || "generic");
  const runtimeConfig =
    (spawnRequest.spawnSpec?.metadata && typeof spawnRequest.spawnSpec.metadata.runtimeConfig === "object")
      ? spawnRequest.spawnSpec.metadata.runtimeConfig
      : {};
  const requestedSessionHandle = String(spawnRequest.sessionHandle || "").trim();
  const capabilities = defaultCapabilitiesForRuntime(runtime, "managed", requestedSessionHandle, runtimeConfig);
  const runtimeState = {
    bridgeInstanceId: BRIDGE_INSTANCE_ID,
    environmentId: environment.id,
    spawnRequestId: spawnRequest.id,
    mode: spawnRequest.mode || "managed-warm",
    resumePolicy: spawnRequest.resumePolicy || "native_first",
  };
  if (requestedSessionHandle) {
    if (runtime === "codex") {
      runtimeState.threadId = requestedSessionHandle;
    } else {
      runtimeState.sessionId = requestedSessionHandle;
    }
  }
  await httpCall("PATCH", `/spawn-requests/${encodeURIComponent(spawnRequest.id)}`, {
    status: "running",
    bridgeId: BRIDGE_INSTANCE_ID,
    processId: String(process.pid),
    sessionHandle: requestedSessionHandle,
    runtimeState,
    capabilities: {
      persistent: true,
      nativeResume: Boolean(requestedSessionHandle) || runtime === "codex" || runtime === "hermes" || runtime === "opencode" || runtime === "pi",
      bridgeResume: true,
      cliAttach: false,
      interrupt: true,
      streaming: true,
      tokenTelemetry: false,
      costTelemetry: false,
      contextReset: true,
    },
    telemetry: {},
  });

  REMOTE_AGENT_STATE.set(spawnRequest.agentId, {
    info: {
      agentId: spawnRequest.agentId,
      role: spawnRequest.role || "coder",
      name: spawnRequest.name || spawnRequest.agentId,
      cwd: workspace,
      model: spawnRequest.spawnSpec?.model || "",
      instructions: spawnRequest.spawnSpec?.instructions || "",
      runtime,
      machineId: MACHINE_ID,
      launchMode: "managed",
      sessionMode: "managed",
      sessionHandle: requestedSessionHandle,
      managedBy: spawnRequest.createdBy || "dashboard",
      capabilities,
      runtimeConfig,
      runtimeState,
    },
  });
  ensureDispatchLoop();
  console.error(`[aify] spawned managed agent "${spawnRequest.agentId}" from request ${spawnRequest.id}`);
}

// THE LOOP SHELL LIVES HERE NOW, with the busy flag it owns. Its gate, its try/catch/finally and
// its body are byte-identical to what left server.js; the only change is that `shutdownStarted`
// arrives as a parameter, because the flag it reads is set by the shutdown chain server.js owns
// and must be read AFRESH on every tick — a value captured at import would be permanently false.
//
// The TIMER stays in server.js: `ensure*Loop` arms it and `cleanupOnExit` clears it, so it has two
// readers and one of them is the shutdown chain.
let spawnLoopBusy = false;
export async function runSpawnLoop({
  CLAIM_OPTS,
  CLAIM_WAIT_MS,
  MACHINE_ID,
  effectiveEnvironmentPayload,
  ensureDispatchLoop,
  shutdownStarted,
}) {
  if (shouldSkipLoop({ eligible: IS_REMOTE && IS_ENVIRONMENT_BRIDGE, alreadyActive: spawnLoopBusy, shuttingDown: shutdownStarted })) return;
  spawnLoopBusy = true;
  try {
    await runSpawnPass({
      CLAIM_OPTS,
      CLAIM_WAIT_MS,
      MACHINE_ID,
      effectiveEnvironmentPayload,
      ensureDispatchLoop,
    });
  } finally {
    spawnLoopBusy = false;
  }
}
