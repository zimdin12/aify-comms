// What a suite run left running, found by its own temp root and killed before the run ends.
//
// Steven, 2026-10-12: "lets make sure we do not leave orphan tests or processes that consume memory or resources".
// The same day four `stub-bin/codex app-server` bash stubs from runs on 09-28 to 10-01 were still alive, each holding
// a node listener, their test long gone. Every file runs with TMPDIR/TEMP/TMP inside this run's root, so whatever a
// test starts from a scratch path names that root on its command line; the root's name is unique to the run.
//
// What this cannot see: a process started with no scratch path on its command line. Its tree is still killed when
// an ancestor named the root.

import { spawnSync } from "node:child_process";

import { parseProcLines } from "../proc-probes.js";
import { terminateProcessTree } from "../runtimes.js";
import { PS_UTF8_PRELUDE } from "../win32-text.js";

//: The only roots this may sweep: a run root the suite made (`aify-comms-testrun-` and its random suffix). Anything
//: else, an empty name above all, would match every process on the machine.
const RUN_ROOT = /^aify-comms-testrun-[A-Za-z0-9]{6,}$/;

//: How far a recorded birth may trail its parent's exit. Windows reports births exactly, so none; `ps` reports whole
//: seconds of age, so a POSIX birth reads up to a second late. ponytail: a pid reused between its file's exit and the
//: runner seeing it (plus that second on POSIX), whose new holder starts a child naming the run root, would still pass.
const BIRTH_SLACK_MS = process.platform === "win32" ? 0 : 1_000;

/**
 * The run's own processes that name `rootName`, outermost first, never `selfPid`. PURE.
 *
 * Naming the root is not ownership: a log viewer someone opened on this run's output names it too, and its parent can be
 * gone, or be another process that held a recorded pid before or after. A named process is the run's when its parent is
 * `selfPid`, is one of `owners` (pid -> that test file's `{ startedAt, exitedAt }`) and the process was born while that
 * file lived, or is another of the run's named processes. A birth the listing could not read is never ownership (review of 0.9.1, F1). A stub whose
 * launcher exited first is missed: missing one is the safe way to be wrong.
 */
export function leftBehind(processes, rootName, selfPid, owners = new Map(), { birthSlackMs = BIRTH_SLACK_MS } = {}) {
  if (!RUN_ROOT.test(String(rootName || ""))) throw new Error(`refusing to sweep for ${JSON.stringify(rootName)}: not a run root`);
  const names = new RegExp(`${rootName}(?![A-Za-z0-9])`);
  const named = new Map(processes.filter((p) => p.pid !== selfPid && names.test(String(p.commandLine || ""))).map((p) => [p.pid, p]));
  const bornToOwner = (p) => {
    const owner = owners.get(p.ppid);
    // Born after the runner asked for the file (its children cannot be older) and before the runner saw it exit.
    return Boolean(owner) && Number.isFinite(p.createdAt) && p.createdAt >= owner.startedAt
      && p.createdAt <= (owner.exitedAt ?? Infinity) + birthSlackMs;
  };
  const ours = (p, seen = new Set()) => {
    if (p.ppid === selfPid || bornToOwner(p)) return true;
    const parent = named.get(p.ppid);
    return Boolean(parent) && !seen.has(parent.pid) && ours(parent, seen.add(p.pid));
  };
  const found = [...named.values()].filter((p) => ours(p));
  const pids = new Set(found.map((p) => p.pid));
  return [...found.filter((p) => !pids.has(p.ppid)), ...found.filter((p) => pids.has(p.ppid))];
}

/** Every process with its birth as epoch ms (`createdAt`, NaN when unreadable); [] when the table cannot be read or
 *  does not hold this process, since a partial table is not the host's. */
export function listWithBirth(run = spawnSync) {
  let rows = [];
  if (process.platform === "win32") {
    const ps = PS_UTF8_PRELUDE + "Get-CimInstance Win32_Process | ForEach-Object { "
      + "$born = if ($_.CreationDate) { ([DateTimeOffset]$_.CreationDate).ToUnixTimeMilliseconds() } else { '' }; "
      + "\"$($_.ProcessId)`t$($_.ParentProcessId)`t$born`t$($_.CommandLine)\" }";
    const res = run("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", ps],
      { encoding: "utf8", windowsHide: true, timeout: 15_000 });
    if (res.error || res.status !== 0) return [];
    rows = parseProcLines(res.stdout).map(({ pid, ppid, commandLine }) => {
      const [born, ...rest] = commandLine.split("\t");
      return { pid, ppid, createdAt: born ? Number(born) : NaN, commandLine: rest.join("\t") };
    });
  } else {
    const res = run("ps", ["-eo", "pid=,ppid=,etimes=,args="], { encoding: "utf8", timeout: 15_000 });
    if (res.error || res.status !== 0) return [];
    const now = Date.now();
    rows = String(res.stdout || "").split("\n").map((line) => line.match(/^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.*)$/)).filter(Boolean)
      .map(([, pid, ppid, age, commandLine]) => ({ pid: Number(pid), ppid: Number(ppid), createdAt: now - Number(age) * 1000, commandLine }));
  }
  return rows.some((row) => row.pid === process.pid) ? rows : [];
}

/** Kill what this run left behind and say so. Returns what it found. Never throws. */
/** Kill a pid and its tree (taskkill /t /f on win32); false when it could not. */
export function killTreeOf(pid) {
  const n = Number(pid);
  if (!Number.isInteger(n) || n <= 0) return false;
  try {
    terminateProcessTree({ pid: n }, "SIGKILL");
    return true;
  } catch {
    return false;
  }
}

export function sweepLeftBehind(rootName, { list = listWithBirth, killTree = killTreeOf,
  log = (line) => console.error(line), selfPid = process.pid, owners = new Map() } = {}) {
  let found = [];
  try { found = leftBehind(list(), rootName, selfPid, owners); } catch { return []; }
  for (const p of found) killTree(p.pid);
  if (found.length) {
    log(`\n[run-all] ${found.length} process(es) from this run were still running; killed:`);
    for (const p of found) log(`  - ${p.pid} ${String(p.commandLine).slice(0, 160)}`);
  }
  return found;
}
