// May THIS process write `runtimeState.bridgeInstanceId` for this agent?
//
// ONE FIELD, TWO MEANINGS, TWO WRITERS. `runtimeState.bridgeInstanceId` is read as "which bridge owns
// this agent" by `managed-orphans`, by `managed-ownership.mjs` and by the service's `bridge_not_current`
// guard. For a RESIDENT agent that is its own MCP bridge, and its sidecar is the right writer. For a
// MANAGED agent it is the ENVIRONMENT BRIDGE that hosts the delivery loop -- a different process
// entirely -- and the agent's own sidecar has no standing to answer it.
//
// Both wrote it anyway. `managed-environment-sync.mjs` and `spawn-loop.mjs` write the environment
// bridge's id; `auto-registration.mjs` and `registration-tool.mjs` write whatever process is
// registering. For a managed agent that is its own per-session sidecar, so the field became a race and
// the last registration won.
//
// OBSERVED ON THE OPERATOR'S HOST, 2026-08-29, on a live and perfectly healthy agent:
//
//     live environment bridge          e720826b-741c-455f-b8bd-e4659777e0c7
//     comms-senior-dev, 03:44          cd17b4c8-ebdb-4980-9492-b2a69435b590
//     comms-senior-dev, minutes later  a0897fff-a6c4-4c99-8349-8b9d750dd22a
//     ef-manager (freshly spawned)     e720826b-741c-455f-b8bd-e4659777e0c7
//
// Two different ids for one agent within minutes, neither the environment bridge's, while an agent the
// environment bridge had just spawned read correctly. `aify-comms doctor` reported that agent as an
// orphaned delivery loop "bound to no live bridge" -- an agent that was answering messages at the time
// -- and its remedy is "restart each named agent, or relaunch the environment bridge". Relaunching the
// bridge reaps the managed fleet. A false alarm whose fix is destructive is worse than no alarm.
//
// THE RULE: a process may claim environment ownership of an agent only when it IS that agent's owner.
// A resident session's own bridge is. A managed agent's per-session sidecar is not, and stays quiet so
// the environment bridge's answer survives.

/**
 * @typedef {object} OwnershipClaimInput
 * @property {string} sessionMode        the mode this registration is establishing
 * @property {boolean} [managedWrapperChild] launched by a managed wrapper (`AIFY_MANAGED_VIA_WRAPPER`)
 * @property {boolean} [isEnvironmentBridge] this process is the environment bridge itself
 */

/**
 * @param {OwnershipClaimInput} input
 * @returns {{claim: boolean, reason: string}}
 *   `reason` is populated on both answers: a caller that logs only refusals teaches nobody why the
 *   field is set on the paths where it IS set.
 */
export function mayClaimEnvironmentOwnership({ sessionMode = "", managedWrapperChild = false } = {}) {
  const mode = String(sessionMode || "").trim().toLowerCase();
  if (mode === "managed" || managedWrapperChild) {
    return {
      claim: false,
      reason: "a managed agent is owned by the environment bridge that hosts its delivery loop, not by "
        + "its own per-session sidecar",
    };
  }
  // RESIDENT, or a mode this does not recognise. Unknown falls to CLAIM deliberately, and this is the
  // one place in this file where the safe direction is the permissive one: a resident session whose
  // sidecar stopped writing the field would leave nothing naming its owner at all, and the guards that
  // read it fail closed on an empty value. A managed agent is the case that must be refused, and
  // managed is the case that is positively identified.
  return { claim: true, reason: `session mode ${mode || "(unset)"} is owned by its own bridge` };
}
