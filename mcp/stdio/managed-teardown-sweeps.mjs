// The managed-teardown SWEEPS: the passes that DECIDE which agents die.
//
// Extracted from server.js in v0.5.4. Deliberately separate from `single-agent-teardown.mjs`, which is TOLD
// its target, and from `reap-managed-survivors.js`, which owns the primitives and makes no decisions. These
// three choose a SET, which is the whole difference in blast radius and the reason they were the last thing
// out of this file.
//
// `confirmedManagedTeardownAgentIds` MOVES WITH THEM because they are its only readers: it is written in
// `runManagedTeardownForBridge` when ownership was resolved freshly, and read by `runManagedTeardownSync`,
// which "may only reuse targets freshly confirmed by runManagedTeardownForBridge. An unexpected exit has no
// safe ownership snapshot, so it reaps nothing and the next boot sweep is the backstop." That fail-closed
// rule is the safety property of this module: an unconfirmed reap is a no-op, never a wrong kill.
//
// `fetchManagedOwnershipForEnv` is INJECTED. It is itself a factory re-binding in server.js, built over
// state (`remoteEffectiveCwdRoots`) whose only writer — `heartbeatEnvironment` — stays there. Passed as a
// value rather than re-derived, so the sweeps see the same reader the rest of the bridge does.

import os from "os";

import { BRIDGE_INSTANCE_ID } from "./bridge-instance.mjs";
import { IS_ENVIRONMENT_BRIDGE } from "./launch-identity.mjs";
import { IS_REMOTE } from "./aify-service-endpoint.mjs";
// `defaultGetCmdline as hermesGetCmdline`: the body uses the LOCAL alias server.js gave it, so the alias
// has to be reproduced here. Importing it under its export name parses fine and fails only on a real
// import — which is what the "every destination is imported by a test" gate caught.
import {
  clearDaemonPid,
  defaultGetCmdline as hermesGetCmdline,
  defaultKillByPort,
  looksLikeHermesProcess,
  stopDaemon,
} from "./hermes-daemon.js";
import { cwdRootsForEnvironment } from "./environment-identity.mjs";
import { resolveFreshManagedTeardownTargets } from "./managed-teardown-ownership.js";
import {
  enumerateManagedSurvivors,
  reapOrphanedManagedSurvivors,
  runManagedTeardown,
  defaultReadMarkers as readManagedMarkers,
} from "./reap-managed-survivors.js";
// The process read side moved to `proc-probes.js` in v0.5.4; imported from its OWNER rather than
// re-exported through the reaper, so a stale import fails here instead of resolving.
import {
  defaultKillTree as killManagedTree,
  defaultListProcesses as listManagedProcesses,
} from "./proc-probes.js";

/**
 * Build the three sweeps over one shared confirmation latch.
 *
 * @param fetchManagedOwnershipForEnv reader for "which managed agents are mine, and is their owner live"
 */
export function createManagedTeardownSweeps({ fetchManagedOwnershipForEnv }) {
  let confirmedManagedTeardownAgentIds = null;

  async function runManagedTeardownForBridge(reason = "bridge teardown") {
    if (!IS_ENVIRONMENT_BRIDGE) return;
    const resolved = await resolveFreshManagedTeardownTargets({
      selfBridgeId: BRIDGE_INSTANCE_ID,
      fetchOwnership: fetchManagedOwnershipForEnv,
      // What we PROVED we owned earlier in this process's life. Used only when the live read
      // fails, which on a full shutdown is the normal case because the service goes down first.
      lastKnownOwnedAgentIds: confirmedManagedTeardownAgentIds,
    });
    const ownedAgentIds = resolved.agentIds;
    // Only remember ownership we actually verified — never overwrite a proven list with a
    // degraded fallback, or one failed read would erode the evidence the next one relies on.
    if (resolved.source === "fresh-ownership") confirmedManagedTeardownAgentIds = ownedAgentIds;
    if (resolved.degraded) {
      console.error(
        `[aify] managed teardown (${reason}): live ownership unavailable (${resolved.error?.message || resolved.error}) — `
        + `falling back to ${ownedAgentIds.length} agent(s) this bridge previously proved it owned: ${ownedAgentIds.join(", ")}`,
      );
    }
    if (resolved.skipped === "ownership-unavailable") {
      console.error(
        `[aify] managed teardown (${reason}): fresh ownership unavailable — reaping nothing (fail-safe):`,
        resolved.error?.message || resolved.error,
      );
      return;
    }
    if (!ownedAgentIds.length) return;
    try {
      const result = runManagedTeardown({
        ownedAgentIds,
        cwdRoots: cwdRootsForEnvironment(),
        listProcesses: listManagedProcesses,
        readMarkers: () => readManagedMarkers(os.tmpdir()),
        // Owned console PTYs are already killed by TERMINAL_MANAGER.stopAll on the
        // graceful path; the detached triad (gateway/loop/daemon) is the survivor
        // concern here, enumerated from markers + the process scan.
        consolePtyPids: [],
        killByPort: defaultKillByPort,
        stopDaemon,
        killTree: killManagedTree,
      });
      if (Array.isArray(result?.pending) && result.pending.length) {
        await Promise.allSettled(result.pending);
      }
      const n =
        (result?.killed?.gatewayHosts?.length || 0) +
        (result?.killed?.deliveryLoops?.length || 0) +
        (result?.killed?.daemons?.length || 0) +
        (result?.killed?.consolePtys?.length || 0);
      if (n) {
        console.error(`[aify] managed teardown (${reason}): reaped ${n} survivor(s) for agents ${ownedAgentIds.join(", ")}`);
      }
      if (result?.errors?.length) {
        console.error(`[aify] managed teardown (${reason}) had ${result.errors.length} error(s):`, JSON.stringify(result.errors));
      }
    } catch (error) {
      console.error(`[aify] managed teardown (${reason}) failed:`, error?.message || error);
    }
  }

  function runManagedTeardownSync(reason = "bridge exit") {
    if (!IS_ENVIRONMENT_BRIDGE) return;
    const ownedAgentIds = Array.isArray(confirmedManagedTeardownAgentIds)
      ? confirmedManagedTeardownAgentIds
      : [];
    if (!ownedAgentIds.length) return;
    try {
      const found = enumerateManagedSurvivors({
        ownedAgentIds,
        cwdRoots: cwdRootsForEnvironment(),
        listProcesses: listManagedProcesses,
        readMarkers: () => readManagedMarkers(os.tmpdir()),
        consolePtyPids: [],
      });
      for (const l of found.deliveryLoops) {
        try { killManagedTree(l.pid); } catch { /* best effort */ }
      }
      for (const d of found.daemons) {
        // ANTI-OVERKILL: a stale daemon-pid marker can name a pid the OS reused for
        // an UNRELATED operator process. Verify the pid's cmdline is hermes before
        // taskkill /t /f; SKIP + log + clear the stale marker otherwise. Mirrors
        // stopDaemon's tracked-pid cross-check (sync path can't await stopDaemon).
        try {
          if (looksLikeHermesProcess(hermesGetCmdline(d.pid))) {
            killManagedTree(d.pid);
          } else {
            console.error(`[aify] managed teardown sync: tracked daemon pid ${d.pid} for agent ${d.agentId} is not hermes — SKIP (stale daemon-pid marker, pid reused)`);
            try { clearDaemonPid(d.agentId, os.tmpdir()); } catch { /* best effort */ }
          }
        } catch { /* best effort */ }
      }
    } catch (error) {
      console.error(`[aify] managed teardown sync (${reason}) failed:`, error?.message || error);
    }
  }

  async function runBootSurvivorSweep() {
    if (!IS_REMOTE || !IS_ENVIRONMENT_BRIDGE) return true;
    let records = null;
    try {
      records = await fetchManagedOwnershipForEnv();
    } catch (error) {
      if (error?.status !== 404) {
        console.error("[aify] boot survivor sweep: ownership query failed — reaping nothing (fail-safe):", error?.message || error);
      }
      return false;
    }
    try {
      const result = reapOrphanedManagedSurvivors({
        selfBridgeId: BRIDGE_INSTANCE_ID,
        cwdRoots: cwdRootsForEnvironment(),
        fetchOwnership: () => records,
        listProcesses: listManagedProcesses,
        readMarkers: () => readManagedMarkers(os.tmpdir()),
        killByPort: defaultKillByPort,
        stopDaemon,
        killTree: killManagedTree,
        // Fresh boot: a survivor whose agent record now reads THIS bridge id is a
        // predecessor's orphan (the heartbeat re-sync can rebind it to self before
        // this sweep reads ownership; a SIGKILL can leave the env row briefly
        // online under the old id). This bridge has spawned no managed children
        // yet, so any running survivor predates the boot and is reapable. A live
        // DIFFERENT bridge's agents are still skipped (owner !== self && ownerLive).
        treatSelfAsOrphan: true,
      });
      if (result?.skipped === "ownership-unavailable") return false;
      if (Array.isArray(result?.pending) && result.pending.length) {
        await Promise.allSettled(result.pending);
      }
      const n =
        (result?.killed?.gatewayHosts?.length || 0) +
        (result?.killed?.deliveryLoops?.length || 0) +
        (result?.killed?.daemons?.length || 0) +
        (result?.killed?.consolePtys?.length || 0);
      if (n) {
        console.error(`[aify] boot survivor sweep: reaped ${n} orphaned managed survivor(s) (owning bridge not live)`);
      }
      if (result?.errors?.length) {
        console.error(`[aify] boot survivor sweep had ${result.errors.length} error(s):`, JSON.stringify(result.errors));
      }
      return true;
    } catch (error) {
      console.error("[aify] boot survivor sweep failed:", error?.message || error);
      return false;
    }
  }

  return { runManagedTeardownForBridge, runManagedTeardownSync, runBootSurvivorSweep };
}
