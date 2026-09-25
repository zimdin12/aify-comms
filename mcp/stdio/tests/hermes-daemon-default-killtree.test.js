// stopDaemon's tracked-pid kill, running its REAL tree-killer against a REAL process tree.
//
// `defaultKillTree` is the default value of stopDaemon's `killTree` parameter, and every other test
// injects a fake there — which is exactly how a default goes unexercised while its call site looks
// thoroughly covered. This establishes that the wiring behind that parameter kills a whole tree.
//
// WHY A REAL TREE, and why this file exists at all: `process-tree.test.js` covers the tree-killer with real
// processes and SKIPS ON WIN32, which is the production platform and the one whose `taskkill /t /f` branch
// carried the 2026-07-10 self-protect bypass. This test runs on both, so the Windows branch stops being the
// unmeasured one.
//
// DETACHED, deliberately. `defaultKillTree` hands `terminateProcessTree` a bare `{ pid }`, so the final
// `proc.kill(signal)` fallback can never fire — there is no `.kill` on a plain object. On POSIX that leaves
// the group kill as the only thing that reaches the parent, and `kill(-pid)` only resolves when the parent
// is a group leader. Real hermes gateways are spawned `detached: true`, so this fixture spawns detached
// too: matching the subject, not making the test easier.

// FIRST: this file writes hermes markers, which must not land in the real %TEMP% when it is run on its own.
import "./_sealed-temp.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import { spawn } from "node:child_process";

import { stopDaemon } from "../hermes-daemon.js";

function isAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return !!err && err.code === "EPERM"; // exists, not ours to signal
  }
}

// A detached parent with TWO grandchildren, and the second one is not padding.
//
// MEASURED on win32: a plain grandchild shares its parent's console, so force-killing the parent alone
// takes it down too — an assertion resting on that child passes against a `taskkill` with NO `/t`, which is
// precisely the flag this test claims to cover. A DETACHED grandchild has its own console and survives
// until something walks the tree. Both are asserted: one for the ordinary case, one that only `/t` reaches.
// DETACHED ONLY OFF WINDOWS. A detached child on Windows gets its OWN CONSOLE, and windowsHide
// suppresses the window being SHOWN rather than the console being created -- with Windows Terminal
// as the host that surfaces as a tab that steals focus. The operator has now reported it three
// times; the previous fix added windowsHide, which treated the symptom.
//
// The fidelity is kept where it is free: on POSIX `detached` makes the child a process GROUP
// LEADER, which is the thing under test. On Windows the tree kill goes through `taskkill /T`,
// which walks the parent-child table and does not care about groups -- so dropping detached there
// costs the test nothing and stops opening windows on a working machine.
const DETACH = process.platform !== "win32";

async function spawnRealTree() {
  const parent = spawn(process.execPath, [
    "-e",
    'const { spawn } = require("node:child_process");' +
    'const plain = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: "ignore" });' +
    'const own = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"],' +
    '  { stdio: "ignore", detached: ' + DETACH + ', windowsHide: true });' +
    'process.stdout.write(plain.pid + " " + own.pid + "\\n");' +
    "setInterval(() => {}, 1000);",
  ], { detached: DETACH, stdio: ["ignore", "pipe", "ignore"], windowsHide: true });

  const [plainPid, detachedPid] = await new Promise((resolve, reject) => {
    const bail = setTimeout(() => reject(new Error("the fixture tree never reported its child pids")), 10000);
    let buf = "";
    parent.stdout.on("data", (chunk) => {
      buf += String(chunk);
      const line = buf.split("\n")[0].trim();
      if (line.split(/\s+/).length === 2) {
        clearTimeout(bail);
        resolve(line.split(/\s+/).map(Number));
      }
    });
    parent.on("error", (err) => { clearTimeout(bail); reject(err); });
  });

  assert.ok(isAlive(parent.pid), "the fixture parent was not alive");
  assert.ok(isAlive(plainPid), "the fixture's plain grandchild was not alive");
  assert.ok(isAlive(detachedPid), "the fixture's detached grandchild was not alive");
  return { parentPid: parent.pid, plainPid, detachedPid, parent };
}

async function waitUntilDead(pid, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!isAlive(pid)) return true;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return false;
}

// Everything around the tracked-pid kill is injected so the subject is the KILLER: no port kill, no
// marker files, no prior-generation reap. killTree and isAlive are DELIBERATELY NOT INJECTED.
function stopWith(readPid, getCmdline) {
  const cleared = [];
  return stopDaemon({
    agentId: "census-default-killtree",
    port: 1,
    killByPort: async () => ({ killed: false }),
    readPid,
    clearPid: (agentId) => { cleared.push(agentId); return true; },
    getCmdline,
    clearGatewayMarkers: () => {},
  }).then((result) => ({ result, cleared }));
}

test("the tracked-pid kill's DEFAULT killer takes down the whole tree", async () => {
  const { parentPid, plainPid, detachedPid, parent } = await spawnRealTree();
  try {
    // The pid-reuse guard reads a real cmdline; the fixture is node, not hermes. Faked so the subject
    // under test is the KILLER — the guard itself is pinned by hermes-daemon's own tests.
    const { result } = await stopWith(() => parentPid, () => "hermes gateway run --replace");
    assert.equal(result.stopped, true, "stopDaemon did not report the kill");
    assert.ok(await waitUntilDead(parentPid), "the tracked daemon's process survived");
    assert.ok(await waitUntilDead(plainPid), "the tracked daemon's child survived");
    assert.ok(await waitUntilDead(detachedPid),
      "the tracked daemon's DETACHED child survived — the killer reached the pid but not the tree");
  } finally {
    for (const pid of [plainPid, detachedPid]) {
      try { process.kill(pid, "SIGKILL"); } catch { /* already gone */ }
    }
    try { parent.kill("SIGKILL"); } catch { /* already gone */ }
  }
});

test("a tracked pid that is NOT alive is left alone, and the marker is still cleared", async () => {
  // The stale-marker case: the daemon died and its pid file outlived it. `defaultIsAlive` (also not
  // injected here) has to answer false for a pid nobody holds, or a recycled pid would take an
  // unrelated tree with it.
  const { parentPid, plainPid, detachedPid, parent } = await spawnRealTree();
  for (const pid of [plainPid, detachedPid]) {
    try { process.kill(pid, "SIGKILL"); } catch { /* already gone */ }
  }
  parent.kill("SIGKILL");
  assert.ok(await waitUntilDead(parentPid), "could not get the fixture pid into a dead state");

  const { result, cleared } = await stopWith(() => parentPid,
    () => { throw new Error("the cmdline must not be read for a dead pid"); });
  assert.equal(result.stopped, false);
  assert.deepEqual(cleared, ["census-default-killtree"]);
});
