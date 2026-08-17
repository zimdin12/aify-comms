// `ensureDaemon`'s kill-prior branch, running its REAL tree-killer against a REAL process tree.
//
// Seventh cluster off the V8-coverage census: `defaultKillTree` had a zero call count. It is the default
// value of `ensureDaemon`'s `killTree` parameter, and every existing test injects a fake there — which is
// exactly how a default goes unexercised while its call site looks thoroughly covered. Nothing had ever
// established that the wiring behind that parameter kills anything.
//
// WHAT IT PROTECTS. `hermes gateway run` daemons proliferate: a crashed one leaves its port abandoned, so
// killByPort on the current port misses it, and the kill-prior branch is the only thing that stops hermes.exe
// piling up per agent. It has to take the whole TREE — the daemon spawns children of its own.
//
// WHY A REAL TREE, and why this file exists at all: `process-tree.test.js` covers the tree-killer with real
// processes and SKIPS ON WIN32, which is the production platform and the one whose `taskkill /t /f` branch
// carried the 2026-07-10 self-protect bypass. This test runs on both, so the Windows branch stops being the
// unmeasured one.
//
// DETACHED, deliberately. `defaultKillTree` hands `terminateProcessTree` a bare `{ pid }`, so the final
// `proc.kill(signal)` fallback can never fire — there is no `.kill` on a plain object. On POSIX that leaves
// the group kill as the only thing that reaches the parent, and `kill(-pid)` only resolves when the parent
// is a group leader. Real hermes daemons are spawned `detached: true` (hermes-daemon.js:237), so this fixture
// spawns detached too: matching the subject, not making the test easier.

// ONE MUTATION SURVIVES THIS FILE: `defaultKillTree` reporting a refused pid as killed. Its boolean has no
// reader at this call site — `killTree(priorPid)` discards it — so nothing here can tell true from false.
// The other kill sites in hermes-daemon.js do consult it and are tested with their own injected fakes.

import assert from "node:assert/strict";
import test from "node:test";
import { spawn } from "node:child_process";

import { ensureDaemon } from "../hermes-daemon.js";

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
async function spawnRealTree() {
  const parent = spawn(process.execPath, [
    "-e",
    'const { spawn } = require("node:child_process");' +
    'const plain = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: "ignore" });' +
    'const own = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"],' +
    '  { stdio: "ignore", detached: true, windowsHide: true });' +
    'process.stdout.write(plain.pid + " " + own.pid + "\\n");' +
    "setInterval(() => {}, 1000);",
  ], { detached: true, stdio: ["ignore", "pipe", "ignore"], windowsHide: true });

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

// A spawn that launches nothing: this test is about the kill, and launching a real `hermes gateway run`
// would leave a daemon behind on the operator's machine.
function fakeSpawn() {
  return { pid: 999_000_001, on() {}, unref() {} };
}

test("the kill-prior branch's DEFAULT killer takes down the whole prior tree", async () => {
  const { parentPid, plainPid, detachedPid, parent } = await spawnRealTree();
  try {
    // probe: down first (so kill-prior runs), up second (so the health poll returns at once).
    let probes = 0;
    const result = await ensureDaemon({
      agentId: "census-default-killtree",
      baseUrl: "http://127.0.0.2:1",
      key: "unused",
      probe: async () => (++probes === 1 ? { available: false } : { available: true, version: "test" }),
      spawn: fakeSpawn,
      // The prior daemon IS the fixture tree.
      readPid: () => parentPid,
      writePid: () => {},
      // The pid-reuse guard reads a real cmdline; the fixture is node, not hermes. Faked so the subject
      // under test is the KILLER — the guard itself is pinned by hermes-daemon's own tests.
      getCmdline: () => "hermes gateway run --replace",
      // killTree and isAlive are DELIBERATELY NOT INJECTED. That is the whole point.
    });

    assert.equal(result.started, true, "ensureDaemon did not reach its spawn path");
    assert.ok(await waitUntilDead(parentPid), "the prior daemon's process survived the kill-prior branch");
    assert.ok(await waitUntilDead(plainPid), "the prior daemon's child survived");
    assert.ok(await waitUntilDead(detachedPid),
      "the prior daemon's DETACHED child survived — the killer reached the pid but not the tree");
  } finally {
    for (const pid of [plainPid, detachedPid]) {
      try { process.kill(pid, "SIGKILL"); } catch { /* already gone */ }
    }
    try { parent.kill("SIGKILL"); } catch { /* already gone */ }
  }
});

test("a prior pid that is NOT alive is left alone, and the spawn still happens", async () => {
  // The stale-marker case: the daemon died and its pid file outlived it. `defaultIsAlive` (also not injected
  // here) has to answer false for a pid nobody holds, or every restart pays a pointless taskkill — and worse,
  // a recycled pid would take an unrelated tree with it.
  const { parentPid, plainPid, detachedPid, parent } = await spawnRealTree();
  for (const pid of [plainPid, detachedPid]) {
    try { process.kill(pid, "SIGKILL"); } catch { /* already gone */ }
  }
  parent.kill("SIGKILL");
  assert.ok(await waitUntilDead(parentPid), "could not get the fixture pid into a dead state");

  let probes = 0;
  const result = await ensureDaemon({
    agentId: "census-default-killtree-stale",
    baseUrl: "http://127.0.0.2:1",
    key: "unused",
    probe: async () => (++probes === 1 ? { available: false } : { available: true, version: "test" }),
    spawn: fakeSpawn,
    readPid: () => parentPid,
    writePid: () => {},
    getCmdline: () => { throw new Error("the cmdline must not be read for a dead pid"); },
  });
  assert.equal(result.started, true);
});

test("no prior pid at all still reaches the spawn", async () => {
  let probes = 0;
  const result = await ensureDaemon({
    agentId: "census-default-killtree-none",
    baseUrl: "http://127.0.0.2:1",
    key: "unused",
    probe: async () => (++probes === 1 ? { available: false } : { available: true, version: "test" }),
    spawn: fakeSpawn,
    readPid: () => 0,
    writePid: () => {},
    getCmdline: () => { throw new Error("the cmdline must not be read when there is no prior pid"); },
  });
  assert.equal(result.started, true);
});
