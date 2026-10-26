// The suite runner kills what a run left running (left-behind.mjs).
//
// THE KILL IS FENCED TO THIS TEST'S OWN PROCESSES. On 2026-10-12 a mutation check of this file removed the filter while
// the test killed with the real tree-kill, and it took down every process on the machine. Here the real kill reaches
// only the sleepers this test spawned; any other pid it is asked to kill is recorded and fails the test.

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import test from "node:test";

import { killTreeOf, leftBehind, sweepLeftBehind } from "./left-behind.mjs";

const ROOT = "aify-comms-testrun-AbC123";

test("ONLY WHAT NAMES THE ROOT is found, outermost first, and never the runner itself", () => {
  const processes = [
    { pid: 10, ppid: 7, createdAt: 50, commandLine: `bash /tmp/${ROOT}/f-x/stub-bin/codex app-server` },
    { pid: 11, ppid: 10, commandLine: `node C:\\Temp\\${ROOT}\\f-x\\serve.js` },
    { pid: 12, ppid: 1, commandLine: "bash /tmp/aify-comms-testrun-Other9/f-y/stub-bin/codex" },
    { pid: 13, ppid: 1, commandLine: `node run-all.mjs ${ROOT}` },
  ];
  assert.deepEqual(leftBehind(processes, ROOT, 13, new Map([[7, { startedAt: 30, exitedAt: 100 }]])).map((p) => p.pid), [10, 11]);
});

test("NAMING THE ROOT IS NOT OWNERSHIP: a viewer someone opened on the run's log, and a longer root, are left alone", () => {
  const processes = [
    { pid: 20, ppid: 1, commandLine: "bash -l" },
    { pid: 21, ppid: 20, commandLine: `tail -f /tmp/${ROOT}/f-x/stdout.log` },
    { pid: 22, ppid: 1, commandLine: `bash /tmp/${ROOT}ZZ/f-x/stub-bin/codex` },
    { pid: 23, ppid: 13, commandLine: `node /tmp/${ROOT}/f-x/serve.js` },
    { pid: 13, ppid: 20, commandLine: "node run-all.mjs" },
  ];
  assert.deepEqual(leftBehind(processes, ROOT, 13).map((p) => p.pid), [23]);
});

test("A VIEWER WHOSE OWN SHELL IS GONE is still not the run's, nor is its child", () => {
  // Review of 0.9.1 F1, second pass: an absent parent was taken as ownership.
  const processes = [
    { pid: 10, ppid: 7, createdAt: 50, commandLine: `bash /tmp/${ROOT}/f-x/stub-bin/codex` },
    { pid: 20, ppid: 88, commandLine: `node C:/tools/view-log.mjs C:/scratch/${ROOT}/stdout.raw` },
    { pid: 21, ppid: 20, commandLine: `node C:/tools/pager.mjs C:/scratch/${ROOT}/stdout.raw` },
  ];
  assert.deepEqual(leftBehind(processes, ROOT, 999, new Map([[7, { startedAt: 30, exitedAt: 100 }]])).map((p) => p.pid), [10]);
});

test("A LATER HOLDER OF A RECORDED PID owns nothing, and a birth nobody could read is never the run's", () => {
  // Review of 0.9.1 F1, third pass: test file 7 exits; a shell later takes pid 7 and starts a viewer on the run's log.
  const processes = [
    { pid: 10, ppid: 7, createdAt: 50, commandLine: `bash /tmp/${ROOT}/f-x/stub-bin/codex` },
    { pid: 20, ppid: 7, createdAt: 5_000, commandLine: `node C:/tools/view-log.mjs C:/scratch/${ROOT}/stdout.raw` },
    { pid: 21, ppid: 20, createdAt: 5_100, commandLine: `node C:/tools/pager.mjs C:/scratch/${ROOT}/stdout.raw` },
    { pid: 30, ppid: 7, createdAt: NaN, commandLine: `bash /tmp/${ROOT}/f-y/stub-bin/codex` },
  ];
  assert.deepEqual(leftBehind(processes, ROOT, 999, new Map([[7, { startedAt: 30, exitedAt: 100 }]])).map((p) => p.pid), [10]);
});

test("A FORMER OR LATER HOLDER OF A RECORDED PID owns nothing, on Windows to the millisecond", () => {
  // Review of 0.9.1 F1, fourth pass. A: a shell held pid 7 before the run's test did, and its viewer survives it.
  // B: a shell takes pid 7 one millisecond after the test exits; Windows births are exact, so no slack lets it in.
  const owners = new Map([[7, { startedAt: 30, exitedAt: 100 }]]);
  const before = [
    { pid: 10, ppid: 7, createdAt: 50, commandLine: `bash /tmp/${ROOT}/f-x/stub-bin/codex` },
    { pid: 20, ppid: 7, createdAt: 10, commandLine: `node C:/tools/view-log.mjs C:/scratch/${ROOT}/stdout.raw` },
    { pid: 21, ppid: 20, createdAt: 11, commandLine: `node C:/tools/pager.mjs C:/scratch/${ROOT}/stdout.raw` },
  ];
  assert.deepEqual(leftBehind(before, ROOT, 999, owners).map((p) => p.pid), [10]);
  const after = [before[0], { ...before[1], createdAt: 102 }, { ...before[2], createdAt: 103 }];
  assert.deepEqual(leftBehind(after, ROOT, 999, owners, { birthSlackMs: 0 }).map((p) => p.pid), [10]);
});

test("A NAME THAT IS NOT A RUN ROOT sweeps nothing, an empty one above all", () => {
  const everything = [{ pid: 10, ppid: 1, commandLine: "anything at all" }];
  const killed = [];
  for (const name of ["", "aify-comms-testrun-", "testrun", "C:\\"]) {
    assert.throws(() => leftBehind(everything, name, 1), /not a run root/);
    assert.deepEqual(sweepLeftBehind(name, { list: () => everything, killTree: (pid) => killed.push(pid), log: () => {} }), []);
  }
  assert.deepEqual(killed, []);
});

const alive = (pid) => { try { process.kill(pid, 0); return true; } catch { return false; } };
const sleeper = (marker) => spawn(process.execPath, ["-e", "setTimeout(() => {}, 120000)", marker], { stdio: "ignore" });

test("A REAL PROCESS LEFT RUNNING is killed, and one from elsewhere is not", async () => {
  const root = `aify-comms-testrun-probe${process.pid}`;
  const leftover = sleeper(root);
  const bystander = sleeper("not-this-run");
  const mine = new Set([leftover.pid, bystander.pid]);
  const strays = [];
  const fencedKill = (pid) => (mine.has(pid) ? killTreeOf(pid) : strays.push(pid));
  try {
    await new Promise((resolve) => setTimeout(resolve, 300));
    const lines = [];
    const found = sweepLeftBehind(root, { killTree: fencedKill, log: (line) => lines.push(line) });
    assert.deepEqual(strays, [], "the sweep tried to kill processes this test did not start");
    assert.deepEqual(found.map((p) => p.pid), [leftover.pid], "the leftover was not found by its root");
    await new Promise((resolve) => setTimeout(resolve, 500));
    assert.equal(alive(leftover.pid), false, "the leftover is still running");
    assert.equal(alive(bystander.pid), true, "a process from elsewhere was killed");
    assert.match(lines.join("\n"), new RegExp(`1 process\\(es\\) from this run were still running; killed:[\\s\\S]*${leftover.pid}`));
  } finally {
    for (const child of [leftover, bystander]) try { child.kill(); } catch { /* already gone */ }
  }
});
