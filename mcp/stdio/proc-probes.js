// Which processes are running, what each one is, and how to kill a tree.
//
// Extracted from `reap-managed-survivors.js` in v0.5.4. Those five helpers were already the most
// widely imported part of that file — `defaultListProcesses` and `defaultKillTree` by the teardown
// sweeps and the single-agent teardown — so they were a shared surface living inside a module named
// for one of its consumers.
//
// CORRECTED 2026-08-16: this note also claimed `parseProcLines` was used here "by
// `reap-managed-claude.js`". That file does not import this module at all — it declares and EXPORTS
// its own byte-identical copy, and has its own test for it. So the extraction did not consolidate
// that helper; it created a second home for it. `parseProcLines-agreement.test.js` pins the two
// against each other until someone rules on which should own it. The claim was the kind that reads
// as a fact and was never checked, which is why the correction is recorded rather than just deleted.
//
// THEY ARE THE READ SIDE OF REAPING, and the split is on that line: enumerate and identify here,
// decide what to kill next door. A reaper that gets identification wrong kills the wrong process
// tree, so this half is worth being able to test without running a reap.
//
// `cmdlineDeliveryLoopAgent` AND `cmdlineResidentAgent` READ A COMMAND LINE TO NAME AN AGENT, which
// is the whole safety argument of the env-scoped reapers: a survivor is only killed when its own
// command line says which agent it belongs to. Matching too loosely kills a co-located agent's
// process; too tightly leaves an orphan holding a session.
//
// Bodies byte-identical to what stood in `reap-managed-survivors.js`.
import { spawnSync } from "child_process";

import { PS_UTF8_PRELUDE } from "./win32-text.js";
import { terminateProcessTree } from "./runtimes.js";

// The managed delivery loop is launched as `... hermes-managed-host.js run <agent>`.
// Return the agent id, or null if this cmdline is not a delivery loop.
export function cmdlineDeliveryLoopAgent(commandLine) {
  const s = String(commandLine || "");
  if (!s) return null;
  // Require the host script AND the `run <agent>` subcommand. `ensure-host` and
  // bare invocations are not long-lived delivery loops.
  // Charset mirrors the service's `SAFE_NAME_RE` (service/api_core/validation.py):
  // `[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}`. The DOT is the part that was missing — the API accepts
  // `team.coder` (verified: 200) and this class stopped at the dot, so the id came back truncated.
  const m = s.match(/hermes-managed-host\.js\s+run\s+([A-Za-z0-9][A-Za-z0-9._-]*)/);
  return m ? m[1] : null;
}


// A resident operator session carries `--aify-agent <x>` (space or = form) and
// is NOT a managed delivery loop. Return the agent id, or null. Used to EXCLUDE
// resident sessions from the survivor set (defence-in-depth — a resident agent
// not in ownedAgentIds is already excluded, but this makes the intent explicit
// and guards a cmdline that carries both markers).
export function cmdlineResidentAgent(commandLine) {
  const s = String(commandLine || "");
  if (!s) return null;
  if (cmdlineDeliveryLoopAgent(s)) return null; // a managed loop is not resident
  // Same charset as above, and for the same reason. A truncated id here is not a cosmetic bug: the
  // caller does `owned.delete(residentAgent)` to enforce "a live resident wrapper owns this triad,
  // never reap any artifact for that agent". Delete a truncated name and the delete misses, so the
  // agent's gateway and daemon artifacts stay reapable while its operator session is live — the
  // guarantee this file's header records an incident for.
  const m = s.match(/--aify-agent[=\s]+([A-Za-z0-9][A-Za-z0-9._-]*)/);
  return m ? m[1] : null;
}


// Enumerate running processes as [{ pid, ppid, commandLine }].
//   - win32: Get-CimInstance Win32_Process (ProcessId + ParentProcessId + CommandLine)
//   - posix: `ps -eo pid=,ppid=,args=`
// Never throws → returns [] on failure.
export function defaultListProcesses(spawnSync = nodeSpawnSync) {
  try {
    if (process.platform === "win32") {
      // PS_UTF8_PRELUDE: survivor matching compares command lines against
      // workspace/wrapper paths; OEM-encoded output mangles non-ASCII profile
      // paths and the match silently misses.
      const ps =
        PS_UTF8_PRELUDE +
        "Get-CimInstance Win32_Process | " +
        "ForEach-Object { \"$($_.ProcessId)`t$($_.ParentProcessId)`t$($_.CommandLine)\" }";
      const res = spawnSync(
        "powershell.exe",
        ["-NoProfile", "-NonInteractive", "-Command", ps],
        { encoding: "utf8", windowsHide: true, timeout: 10000 },
      );
      return parseProcLines(String(res.stdout || ""));
    }
    const res = spawnSync("ps", ["-eo", "pid=,ppid=,args="], {
      encoding: "utf8",
      timeout: 10000,
    });
    return String(res.stdout || "")
      .split(/\r?\n/)
      .map((line) => {
        const m = line.match(/^\s*(\d+)\s+(\d+)\s+(.*)$/);
        if (!m) return null;
        return { pid: Number(m[1]), ppid: Number(m[2]), commandLine: m[3] };
      })
      .filter(Boolean);
  } catch {
    return [];
  }
}


// Parse "PID\tPPID\tCOMMANDLINE" lines (win path). Exported for tests.
export function parseProcLines(stdout) {
  return String(stdout || "")
    .split(/\r?\n/)
    .map((line) => {
      const parts = line.split("\t");
      if (parts.length < 3) return null;
      const pid = Number(parts[0].trim());
      const ppid = Number(parts[1].trim());
      const commandLine = parts.slice(2).join("\t");
      if (!Number.isInteger(pid) || pid <= 0) return null;
      return { pid, ppid: Number.isInteger(ppid) ? ppid : 0, commandLine };
    })
    .filter(Boolean);
}


// Kill a pid + its process tree (taskkill /t /f on win32). Never throws.
export function defaultKillTree(pid) {
  const n = Number(pid);
  if (!Number.isInteger(n) || n <= 0) return false;
  try {
    terminateProcessTree({ pid: n }, "SIGKILL");
    return true;
  } catch {
    return false;
  }
}
