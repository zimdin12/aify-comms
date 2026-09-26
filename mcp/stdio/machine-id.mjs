// The machine id a bridge registers with: "<platform>:<host>", lowercased. Its own module, and not in
// runtimes.js, because a runtime hook (agent-state-event.mjs) sends it on every event and runtimes.js
// pulls in every adapter: about 90 ms of imports per hook for three small functions.

import os from "os";
import fs from "fs";

// Deterministic platform tag for machine_id, generic for ANY machine. WSL does
// NOT propagate WSL_DISTRO_NAME to every spawn context (present in interactive
// shells, absent in many child processes), so deriving the tag from it made the
// SAME machine register as both `wsl-<distro>:host` (env present) and
// `linux:host` (env absent). That divergence broke the machine_id match in
// dispatch-claim + bridge supersession — a WSL delivery loop registered as
// `wsl-ubuntu:host` could never claim runs for an agent recorded as
// `linux:host`, so deliveries sat queued forever (observed 2026-06-02). Detect
// WSL from /proc (visible to EVERY process on the machine, independent of env)
// and emit a STABLE `wsl` tag; native platforms keep process.platform. The host
// component is still the machine's own hostname, so this stays fully dynamic
// across everyone's PCs — nothing is hardcoded.
// EXPORTED because a second reader needed it and copied the wrong input instead. `environmentKind`
// in environment-identity.mjs derived WSL from WSL_DISTRO_NAME -- the very variable the comment
// above explains is absent in many child processes -- and the environment ID is built from that
// kind. So the same WSL host could register `wsl:<host>:default` from an interactive shell and
// `linux:<host>:default` from a spawned one, which is the 2026-06-02 divergence again, one field
// over, on the key an environment row is matched on. One predicate, two readers.
export function hostIsWsl({ platform = process.platform, readFile = fs.readFileSync } = {}) {
  if (platform !== "linux") return false;
  try {
    return /microsoft|wsl/i.test(readFile("/proc/sys/kernel/osrelease", "utf8"));
  } catch {
    // /proc unreadable -> not WSL. Fails closed: a host we cannot prove is WSL keeps its platform.
    return false;
  }
}

function stablePlatformTag() {
  return hostIsWsl() ? "wsl" : process.platform;
}

export function defaultMachineId() {
  let host =
    process.env.AIFY_MACHINE_ID ||
    process.env.COMPUTERNAME ||
    process.env.HOSTNAME ||
    "";
  if (!host) {
    try {
      host = os.hostname() || "";
    } catch {
      // ignore and fall through to unknown-host
    }
  }
  host = host || "unknown-host";
  const wsl = stablePlatformTag();
  // Lowercase the whole "<platform>:<host>" id. Hostnames report with
  // inconsistent casing across launch paths (e.g. win32:DevBox-1 vs
  // win32:DEVBOX-1); the service compares machine_id case-insensitively
  // for bridge supersession, so send a consistent (lowercased) value.
  return `${wsl}:${host}`.toLowerCase();
}
