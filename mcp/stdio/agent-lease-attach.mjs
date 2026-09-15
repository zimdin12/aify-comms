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

/** The argv for one attach, or null when there is no lease to join or nothing to attach. */
export function leaseAttachArgv({ env, pid, kind, helper }) {
  const agentId = String(env?.AIFY_AGENT_ID || "").trim();
  const instance = Number(env?.AIFY_AGENT_LEASE);
  if (!agentId || !Number.isInteger(instance) || instance <= 0 || !Number.isInteger(pid) || pid <= 0) return null;
  return [helper, "attach", "--agent", agentId, "--instance", String(instance), "--pid", String(pid), "--kind", String(kind || "")];
}

/** Attach `pid` to the lease this process's launcher holds. Returns whether an attach was started. Never throws. */
export function attachToAgentLease({ pid, kind, env = process.env, spawn = nodeSpawn, helper } = {}) {
  try {
    const argv = leaseAttachArgv({ env, pid, kind, helper: helper || defaultHelper() });
    if (!argv) return false;
    const child = spawn(process.execPath, argv, { detached: true, stdio: "ignore", windowsHide: true });
    child.on?.("error", () => {});
    child.unref?.();
    return true;
  } catch {
    return false;
  }
}
