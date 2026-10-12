// What a suite run left running, found by its own temp root and killed before the run ends.
//
// Steven, 2026-10-12: "lets make sure we do not leave orphan tests or processes that consume memory or resources".
// The same day four `stub-bin/codex app-server` bash stubs from runs on 09-28 to 10-01 were still alive, each holding
// a node listener, their test long gone. Every file runs with TMPDIR/TEMP/TMP inside this run's root, so whatever a
// test starts from a scratch path names that root on its command line; the root's name is unique to the run.
//
// What this cannot see: a process started with no scratch path on its command line. Its tree is still killed when
// an ancestor named the root.

import { defaultKillTree, defaultListProcesses } from "../proc-probes.js";

//: The only roots this may sweep: a run root the suite made (`aify-comms-testrun-` and its random suffix). Anything
//: else, an empty name above all, would match every process on the machine.
const RUN_ROOT = /^aify-comms-testrun-[A-Za-z0-9]{6,}$/;

/**
 * The run's own processes that name `rootName`, outermost first, never `selfPid`. PURE.
 *
 * Naming the root is not ownership: a log viewer someone opened on this run's output names it too, and its parent can be
 * gone as well. A named process is the run's when its parent is `selfPid`, one of `owners` (the test files the runner
 * spawned and recorded, which is how a leaked stub looks once its test has exited), or another of the run's named
 * processes. ponytail: a stub whose launcher exited first is missed (its parent was neither); missing one is the safe way
 * to be wrong (review of 0.9.1, F1).
 */
export function leftBehind(processes, rootName, selfPid, owners = new Set()) {
  if (!RUN_ROOT.test(String(rootName || ""))) throw new Error(`refusing to sweep for ${JSON.stringify(rootName)}: not a run root`);
  const names = new RegExp(`${rootName}(?![A-Za-z0-9])`);
  const named = new Map(processes.filter((p) => p.pid !== selfPid && names.test(String(p.commandLine || ""))).map((p) => [p.pid, p]));
  const ours = (p, seen = new Set()) => {
    if (p.ppid === selfPid || owners.has(p.ppid)) return true;
    const parent = named.get(p.ppid);
    return Boolean(parent) && !seen.has(parent.pid) && ours(parent, seen.add(p.pid));
  };
  const found = [...named.values()].filter((p) => ours(p));
  const pids = new Set(found.map((p) => p.pid));
  return [...found.filter((p) => !pids.has(p.ppid)), ...found.filter((p) => pids.has(p.ppid))];
}

/** Kill what this run left behind and say so. Returns what it found. Never throws. */
export function sweepLeftBehind(rootName, { list = defaultListProcesses, killTree = defaultKillTree,
  log = (line) => console.error(line), selfPid = process.pid, owners = new Set() } = {}) {
  let found = [];
  try { found = leftBehind(list(), rootName, selfPid, owners); } catch { return []; }
  for (const p of found) killTree(p.pid);
  if (found.length) {
    log(`\n[run-all] ${found.length} process(es) from this run were still running; killed:`);
    for (const p of found) log(`  - ${p.pid} ${String(p.commandLine).slice(0, 160)}`);
  }
  return found;
}
