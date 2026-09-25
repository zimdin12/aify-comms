#!/usr/bin/env node
// Unit tests for stopDaemon, the port kill and the pid helpers. Every kill, cmdline lookup and
// listener resolution is INJECTED: no real hermes process is touched.

// FIRST: this file writes hermes markers, which must not land in the real %TEMP% when it is run on its own.
import "./_sealed-temp.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  stopDaemon,
  readDaemonPid,
  writeDaemonPid,
  clearDaemonPid,
  defaultKillByPort,
  looksLikeHermesProcess,
} from "../hermes-daemon.js";
import { agentEndpoint } from "../hermes-endpoint.js";
import { tmpDir } from "./_tmpdir.js";

function makeTempDir() {
  return tmpDir("hermes-daemon-test-");
}
function cleanup(dir) {
  try {
    fs.rmSync(dir, { recursive: true, force: true });
  } catch {
    /* best-effort */
  }
}

// A fake child handle: records whether unref() was called.
test("stopDaemon: resolves the agent's port and calls killByPort with it", async () => {
  const dir = makeTempDir();
  try {
    const ep = agentEndpoint("stop-me", { tempDir: dir });
    const calls = [];
    const killByPort = async (port) => {
      calls.push(port);
      return { killed: true, pid: 5151 };
    };
    const result = await stopDaemon({ agentId: "stop-me", tempDir: dir, killByPort });
    assert.equal(calls.length, 1, "killByPort must be called exactly once");
    assert.equal(calls[0], ep.port, "killByPort must receive the agent's resolved port");
    assert.equal(result.stopped, true);
    assert.equal(result.pid, 5151);
  } finally {
    cleanup(dir);
  }
});

test("stopDaemon: clears the agent's port/key gateway markers (terminal teardown, Task 4.1)", async () => {
  const dir = makeTempDir();
  try {
    const fs = await import("node:fs");
    const path = await import("node:path");
    agentEndpoint("clear-me", { tempDir: dir }); // writes key marker
    fs.writeFileSync(path.join(dir, "aify-hermes-port-clear-me"), "8765"); // port marker
    const killByPort = async () => ({ killed: false });
    await stopDaemon({ agentId: "clear-me", tempDir: dir, killByPort });
    assert.ok(!fs.existsSync(path.join(dir, "aify-hermes-port-clear-me")), "port marker cleared on stop");
    assert.ok(!fs.existsSync(path.join(dir, "aify-hermes-key-clear-me")), "key marker cleared on stop");
  } finally {
    cleanup(dir);
  }
});

test("stopDaemon: idempotent when no process on the port (not-found → stopped:false, no throw)", async () => {
  const dir = makeTempDir();
  try {
    const killByPort = async () => ({ killed: false });
    const result = await stopDaemon({ agentId: "absent", tempDir: dir, killByPort });
    assert.equal(result.stopped, false, "no daemon on port → stopped:false");
    assert.equal(result.pid, undefined);
  } finally {
    cleanup(dir);
  }
});

test("stopDaemon: never throws even when killByPort rejects", async () => {
  const dir = makeTempDir();
  try {
    const killByPort = async () => {
      throw new Error("boom");
    };
    let result;
    await assert.doesNotReject(async () => {
      result = await stopDaemon({ agentId: "throwy", tempDir: dir, killByPort });
    });
    assert.equal(result.stopped, false, "a kill error must resolve to stopped:false, not throw");
  } finally {
    cleanup(dir);
  }
});

test("stopDaemon: explicit endpoint port wins over agentId derivation", async () => {
  let seen;
  const killByPort = async (port) => {
    seen = port;
    return { killed: false };
  };
  await stopDaemon({ endpoint: { port: 9999 }, killByPort });
  assert.equal(seen, 9999, "explicit endpoint.port must be used");
});

// --- per-agent daemon pid tracking + kill-prior ----------------------------

// Records killTree(pid) calls. Default isAlive: every nonzero pid is alive.
function recordingKillTree() {
  const calls = [];
  const fn = (pid) => {
    calls.push(pid);
    return true;
  };
  fn.calls = calls;
  return fn;
}

test("pid helpers: write → read round-trips; clear removes the file", async () => {
  const dir = makeTempDir();
  try {
    assert.equal(readDaemonPid("pidder", dir), undefined, "no file → undefined");
    assert.equal(writeDaemonPid("pidder", 12345, dir), true);
    assert.equal(readDaemonPid("pidder", dir), 12345, "round-trips the pid");
    assert.equal(clearDaemonPid("pidder", dir), true);
    assert.equal(readDaemonPid("pidder", dir), undefined, "cleared → undefined");
  } finally {
    cleanup(dir);
  }
});

test("stopDaemon: kills by port AND by tracked pid, then clears the pid file", async () => {
  const dir = makeTempDir();
  try {
    const ep = agentEndpoint("stop-both", { tempDir: dir });
    writeDaemonPid("stop-both", 7373, dir);
    const portCalls = [];
    const killByPort = async (port) => {
      portCalls.push(port);
      return { killed: true, pid: 6262 };
    };
    const killTree = recordingKillTree();
    const result = await stopDaemon({
      agentId: "stop-both",
      tempDir: dir,
      killByPort,
      killTree,
      isAlive: (pid) => pid === 7373,
      getCmdline: () => "hermes gateway run --replace", // confirmed hermes → tracked-pid kill proceeds
    });
    assert.equal(portCalls.length, 1, "killByPort must be called");
    assert.equal(portCalls[0], ep.port, "killByPort gets the agent's port");
    assert.equal(killTree.calls.length, 1, "tracked pid must also be killed");
    assert.equal(killTree.calls[0], 7373, "kill-tree gets the tracked daemon pid");
    assert.equal(result.stopped, true);
    assert.equal(readDaemonPid("stop-both", dir), undefined, "pid file must be cleared");
  } finally {
    cleanup(dir);
  }
});

test("stopDaemon: tracked pid not alive → no kill-tree, still clears pid file", async () => {
  const dir = makeTempDir();
  try {
    writeDaemonPid("stop-dead", 2222, dir);
    const killByPort = async () => ({ killed: false });
    const killTree = recordingKillTree();
    const result = await stopDaemon({
      agentId: "stop-dead",
      tempDir: dir,
      killByPort,
      killTree,
      isAlive: () => false,
    });
    assert.equal(killTree.calls.length, 0, "dead tracked pid must not be signalled");
    assert.equal(result.stopped, false);
    assert.equal(readDaemonPid("stop-dead", dir), undefined, "pid file cleared regardless");
  } finally {
    cleanup(dir);
  }
});

// --- anti-overkill cmdline cross-checks (port/pid reuse safety) -------------

test("looksLikeHermesProcess: matches a hermes cmdline/image, rejects unrelated/empty", () => {
  assert.equal(looksLikeHermesProcess("C:\\Python\\Scripts\\hermes.exe gateway run --replace"), true);
  assert.equal(looksLikeHermesProcess("/usr/bin/hermes gateway run"), true);
  assert.equal(looksLikeHermesProcess("hermes\tC:\\...\\hermes.exe\thermes.exe"), true, "tab-joined cmdline/path/name form");
  assert.equal(looksLikeHermesProcess("HERMES dashboard --tui"), true, "case-insensitive");
  assert.equal(looksLikeHermesProcess("node C:\\proj\\dev-server.js --port 9342"), false, "unrelated dev server");
  assert.equal(looksLikeHermesProcess("python -m http.server 9342"), false);
  assert.equal(looksLikeHermesProcess(""), false, "empty/unknown → fail-safe no-match");
  assert.equal(looksLikeHermesProcess(undefined), false);
});

test("killByPort: SKIPS a listener whose cmdline is not hermes (port reused by unrelated process)", async () => {
  const killed = [];
  const result = await defaultKillByPort(9342, {
    resolveListenerPids: async () => [55555], // some unrelated process now owns the port
    getCmdline: () => "node C:\\proj\\dev-server.js --port 9342", // NOT hermes
    killOnePid: async (pid) => { killed.push(pid); return true; },
  });
  assert.equal(killed.length, 0, "must NOT kill an unrelated process that recycled the port");
  assert.equal(result.killed, false);
  assert.equal(result.skipped, true);
});

test("killByPort: KILLS a listener whose cmdline IS hermes", async () => {
  const killed = [];
  const result = await defaultKillByPort(9342, {
    resolveListenerPids: async () => [4242],
    getCmdline: () => "C:\\Python\\Scripts\\hermes.exe gateway run --replace",
    killOnePid: async (pid) => { killed.push(pid); return true; },
  });
  assert.deepEqual(killed, [4242], "the hermes listener must be killed");
  assert.equal(result.killed, true);
  assert.equal(result.pid, 4242);
});

test("killByPort: no listener on the port → { killed:false }, no kill", async () => {
  const killed = [];
  const result = await defaultKillByPort(9342, {
    resolveListenerPids: async () => [],
    getCmdline: () => { throw new Error("should not be consulted"); },
    killOnePid: async (pid) => { killed.push(pid); return true; },
  });
  assert.equal(killed.length, 0);
  assert.equal(result.killed, false);
});

test("stopDaemon: SKIPS the tracked-pid kill when the pid's cmdline is not hermes (pid reused)", async () => {
  const dir = makeTempDir();
  try {
    writeDaemonPid("reused", 31337, dir);
    const killByPort = async () => ({ killed: false });
    const killTree = recordingKillTree();
    const result = await stopDaemon({
      agentId: "reused",
      tempDir: dir,
      killByPort,
      killTree,
      isAlive: () => true, // pid IS alive — but it is NOT hermes
      getCmdline: () => "node C:\\proj\\dev-server.js", // unrelated operator process reused the pid
    });
    assert.equal(killTree.calls.length, 0, "must NOT killTree an unrelated process under pid reuse");
    assert.equal(result.stopped, false);
    assert.equal(readDaemonPid("reused", dir), undefined, "stale pid marker is cleared even when skipped");
  } finally {
    cleanup(dir);
  }
});

test("stopDaemon: KILLS the tracked pid when its cmdline IS hermes", async () => {
  const dir = makeTempDir();
  try {
    writeDaemonPid("realhermes", 7373, dir);
    const killByPort = async () => ({ killed: false });
    const killTree = recordingKillTree();
    const result = await stopDaemon({
      agentId: "realhermes",
      tempDir: dir,
      killByPort,
      killTree,
      isAlive: (pid) => pid === 7373,
      getCmdline: () => "C:\\Python\\Scripts\\hermes.exe gateway run --replace",
    });
    assert.deepEqual(killTree.calls, [7373], "a confirmed-hermes tracked pid must be killed");
    assert.equal(result.stopped, true);
    assert.equal(readDaemonPid("realhermes", dir), undefined, "pid file cleared");
  } finally {
    cleanup(dir);
  }
});
