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

/** The processes whose command line names `rootName`, outermost first, never `selfPid`. PURE. */
export function leftBehind(processes, rootName, selfPid) {
  if (!RUN_ROOT.test(String(rootName || ""))) throw new Error(`refusing to sweep for ${JSON.stringify(rootName)}: not a run root`);
  const named = processes.filter((p) => p.pid !== selfPid && String(p.commandLine || "").includes(rootName));
  const pids = new Set(named.map((p) => p.pid));
  return [...named.filter((p) => !pids.has(p.ppid)), ...named.filter((p) => pids.has(p.ppid))];
}

/** Kill what this run left behind and say so. Returns what it found. Never throws. */
export function sweepLeftBehind(rootName, { list = defaultListProcesses, killTree = defaultKillTree,
  log = (line) => console.error(line), selfPid = process.pid } = {}) {
  let found = [];
  try { found = leftBehind(list(), rootName, selfPid); } catch { return []; }
  for (const p of found) killTree(p.pid);
  if (found.length) {
    log(`\n[run-all] ${found.length} process(es) from this run were still running; killed:`);
    for (const p of found) log(`  - ${p.pid} ${String(p.commandLine).slice(0, 160)}`);
  }
  return found;
}
