// The environment-control claim pass, extracted from server.js in v0.5.4.
//
// The LOOP stays in server.js — its timer, its busy flag, its shutdown gate and its catch/finally are
// untouched. Only the pass moved, byte-identical, so nothing about when it runs or how often changed.
//
// It claims one environment control at a time and acts on it, and one of those actions is obeying a
// SUPERSEDE request from the service — that path ends in `shutdownWithStatus`, which is why the
// dependency is injected rather than reached for: a module that could stop the bridge on its own is a
// worse thing to own than one that is handed the ability.

import { httpCall } from "./aify-service-endpoint.mjs";
import { BRIDGE_INSTANCE_ID } from "./bridge-instance.mjs";
import { noteControlClaimSuccess } from "./claim-failure-tracker.mjs";
import { IS_ENVIRONMENT_BRIDGE } from "./launch-identity.mjs";
import { IS_REMOTE } from "./aify-service-endpoint.mjs";
import { shouldSkipLoop } from "./loop-gate.mjs";

export async function runEnvironmentControlPass({
  CLAIM_OPTS,
  CLAIM_WAIT_MS,
  MACHINE_ID,
  effectiveEnvironmentPayload,
  shutdownWithStatus,
}) {
  const environment = effectiveEnvironmentPayload();
  const claim = await httpCall("POST", "/environments/controls/claim", {
    environmentId: environment.id,
    bridgeId: BRIDGE_INSTANCE_ID,
    machineId: MACHINE_ID,
    waitMs: CLAIM_WAIT_MS,
  }, CLAIM_OPTS);
  noteControlClaimSuccess("environment controls");
  const control = claim?.control;
  if (!control) return;
  if (control.action === "stop") {
    const current = control.currentEnvironment || {};
    const currentMeta = current.metadata || {};
    if (control.requestedBy === "server:superseded-bridge" && current.bridgeId && current.bridgeId !== BRIDGE_INSTANCE_ID) {
      const replacementBits = [
        `replacement bridge ${current.bridgeId}`,
        currentMeta.pid ? `pid ${currentMeta.pid}` : "",
        currentMeta.cwd ? `cwd ${currentMeta.cwd}` : "",
      ].filter(Boolean).join(", ");
      console.error(`[aify] environment ${environment.id} was superseded by ${replacementBits}; this older bridge (${BRIDGE_INSTANCE_ID}) is exiting`);
    } else {
      console.error(`[aify] environment stop requested for ${environment.id}; bridge exiting`);
    }
    try {
      await httpCall("PATCH", `/environments/controls/${encodeURIComponent(control.id)}`, {
        status: "completed",
      });
    } catch {
      // The process is going down anyway; best effort.
    }
    // Supersede / env-stop path: route through shutdownWithStatus so the WS2
    // managed-triad teardown (runManagedTeardownForBridge) reaps this older
    // bridge's detached survivors before it exits — same clean-slate guarantee
    // as a SIGINT/SIGTERM restart.
    setTimeout(() => { shutdownWithStatus(0); }, 50);
    return;
  }
  await httpCall("PATCH", `/environments/controls/${encodeURIComponent(control.id)}`, {
    status: "failed",
    error: `Unsupported environment control action: ${control.action}`,
  });
}

// THE LOOP SHELL LIVES HERE NOW, with the busy flag it owns. Its gate, its try/catch/finally and
// its body are byte-identical to what left server.js; the only change is that `shutdownStarted`
// arrives as a parameter, because the flag it reads is set by the shutdown chain server.js owns
// and must be read AFRESH on every tick — a value captured at import would be permanently false.
//
// The TIMER stays in server.js: `ensure*Loop` arms it and `cleanupOnExit` clears it, so it has two
// readers and one of them is the shutdown chain.
let environmentControlBusy = false;
export async function runEnvironmentControlLoop({
  CLAIM_OPTS,
  CLAIM_WAIT_MS,
  MACHINE_ID,
  effectiveEnvironmentPayload,
  shutdownStarted,
  shutdownWithStatus,
}) {
  if (shouldSkipLoop({ eligible: IS_REMOTE && IS_ENVIRONMENT_BRIDGE, alreadyActive: environmentControlBusy, shuttingDown: shutdownStarted })) return;
  environmentControlBusy = true;
  try {
    await runEnvironmentControlPass({
      CLAIM_OPTS,
      CLAIM_WAIT_MS,
      MACHINE_ID,
      effectiveEnvironmentPayload,
      shutdownWithStatus,
    });
  } catch (error) {
    if (error?.status !== 404) {
      noteControlClaimFailure("environment controls", error);
    }
  } finally {
    environmentControlBusy = false;
  }
}
