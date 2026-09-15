// A process started DETACHED joins its agent's lease (aify-wrapper's agent-lease library).
//
// The launcher claims the lease and exports AIFY_AGENT_LEASE (its own pid). Its runtime is in its process
// tree on Windows and its process group on POSIX, so stopping the launcher stops the runtime. A detached
// process is in neither: the hermes gateway host is spawned `detached: true` on purpose so it outlives the
// short-lived ensure-host CLI, and that is exactly the process that held a session lease for two days in
// 2026-09. Attaching it is what lets the next claim find it.
//
// FIRE AND FORGET. The attach runs the wrapper's CLI in a detached child, so the caller -- ensure-host,
// which the wrapper waits on before starting the TUI -- is not held by the lock or by the start-time probe,
// and a failure costs the guarantee, never the launch.

import { spawn as nodeSpawn } from "node:child_process";
import { fileURLToPath } from "node:url";

function defaultHelper() {
  return fileURLToPath(import.meta.resolve("aify-wrapper/bin/aify-agent-lease.mjs"));
}

/**
 * The argv for one attach of `agentId`'s process, or null when there is no lease to join or nothing to
 * attach. The agent is the CALLER'S, never read from the environment: the lease this process inherited
 * belongs to whichever agent's launcher started it, and a gateway attached to another agent's lease is
 * killed by that agent's next replace. An inherited identity naming a different agent means no attach.
 */
export function leaseAttachArgv({ env, agentId, pid, kind, helper }) {
  const agent = String(agentId || "").trim();
  const inheritedAgent = String(env?.AIFY_AGENT_ID || "").trim();
  const instance = Number(env?.AIFY_AGENT_LEASE);
  if (!agent || (inheritedAgent && inheritedAgent !== agent)) return null;
  if (!Number.isInteger(instance) || instance <= 0 || !Number.isInteger(pid) || pid <= 0) return null;
  return [helper, "attach", "--agent", agent, "--instance", String(instance), "--pid", String(pid), "--kind", String(kind || "")];
}

/** Attach `agentId`'s `pid` to the lease this process's launcher holds. Returns whether an attach was started. Never throws. */
export function attachToAgentLease({ agentId, pid, kind, env = process.env, spawn = nodeSpawn, helper } = {}) {
  try {
    const argv = leaseAttachArgv({ env, agentId, pid, kind, helper: helper || defaultHelper() });
    if (!argv) return false;
    const child = spawn(process.execPath, argv, { detached: true, stdio: "ignore", windowsHide: true });
    child.on?.("error", () => {});
    child.unref?.();
    return true;
  } catch {
    return false;
  }
}
