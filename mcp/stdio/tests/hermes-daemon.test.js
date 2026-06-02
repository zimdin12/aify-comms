#!/usr/bin/env node
// Unit tests for ensureDaemon — the idempotent "one api_server daemon up"
// helper. All spawn/probe are INJECTED: no real hermes process is launched
// and no real network is touched.
// Contract: docs/superpowers/specs/2026-05-30-hermes-apiserver-contract.md.

import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  ensureDaemon,
  stopDaemon,
  readDaemonPid,
  writeDaemonPid,
  clearDaemonPid,
  defaultKillByPort,
  looksLikeHermesProcess,
} from "../hermes-daemon.js";
import { agentEndpoint } from "../hermes-endpoint.js";

function makeTempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "hermes-daemon-test-"));
}
function cleanup(dir) {
  try {
    fs.rmSync(dir, { recursive: true, force: true });
  } catch {
    /* best-effort */
  }
}

// A fake child handle: records whether unref() was called.
function fakeChild(pid = 4242) {
  return {
    pid,
    unrefed: false,
    unref() {
      this.unrefed = true;
    },
  };
}

// Build an injectable spawn that records its calls and returns a fake child.
function recordingSpawn(child = fakeChild()) {
  const calls = [];
  const spawn = (cmd, args, opts) => {
    calls.push({ cmd, args, opts });
    return child;
  };
  spawn.calls = calls;
  return spawn;
}

// A probe that returns the given sequence of results, one per call; the last
// value is repeated once the sequence is exhausted.
function sequencedProbe(results) {
  let i = 0;
  const probe = async () => {
    const r = results[Math.min(i, results.length - 1)];
    i += 1;
    return r;
  };
  probe.callCount = () => i;
  return probe;
}

test("already-up: returns {started:false} and never spawns", async () => {
  const spawn = recordingSpawn();
  const probe = sequencedProbe([{ available: true, version: "0.15.1" }]);
  const result = await ensureDaemon({
    key: "secret",
    spawn,
    probe,
  });
  assert.equal(result.started, false);
  assert.equal(result.version, "0.15.1");
  assert.equal(spawn.calls.length, 0, "spawn must NOT be called when already up");
});

test("down-then-up: spawns once with gateway run + API_SERVER_* env, returns {started:true}", async () => {
  const child = fakeChild(9001);
  const spawn = recordingSpawn(child);
  // unavailable first (pre-spawn probe), available on the next poll.
  const probe = sequencedProbe([
    { available: false, reason: "daemon not running" },
    { available: true, version: "0.15.1" },
  ]);

  const result = await ensureDaemon({
    key: "s3cr3t",
    port: 8642,
    host: "127.0.0.1",
    hermesCmd: "hermes",
    spawn,
    probe,
    healthTimeoutMs: 2000,
    pollMs: 10,
  });

  assert.equal(result.started, true);
  assert.equal(result.version, "0.15.1");
  assert.equal(result.pid, 9001);

  assert.equal(spawn.calls.length, 1, "spawn must be called exactly once");
  const call = spawn.calls[0];
  assert.equal(call.cmd, "hermes");
  assert.deepEqual(call.args, ["gateway", "run", "--replace"]);
  assert.equal(call.opts.detached, true);
  assert.equal(call.opts.stdio, "ignore");
  assert.equal(call.opts.env.API_SERVER_ENABLED, "1");
  assert.equal(call.opts.env.API_SERVER_KEY, "s3cr3t");
  assert.equal(call.opts.env.API_SERVER_PORT, "8642");
  assert.equal(call.opts.env.API_SERVER_HOST, "127.0.0.1");
  assert.equal(child.unrefed, true, "child must be unref()'d so it outlives the bridge");
});

test("never-up: throws a clear timeout error", async () => {
  const spawn = recordingSpawn();
  const probe = sequencedProbe([{ available: false, reason: "daemon not running" }]);

  await assert.rejects(
    () =>
      ensureDaemon({
        key: "k",
        spawn,
        probe,
        healthTimeoutMs: 50,
        pollMs: 10,
      }),
    (err) => {
      assert.match(err.message, /did not become healthy/i);
      assert.match(err.message, /50ms/);
      return true;
    },
  );
  // It still attempted to start exactly one daemon.
  assert.equal(spawn.calls.length, 1);
});

test("idempotent: two calls when already up spawn zero times", async () => {
  const spawn = recordingSpawn();
  const probe = sequencedProbe([{ available: true, version: "0.15.1" }]);

  const a = await ensureDaemon({ key: "k", spawn, probe });
  const b = await ensureDaemon({ key: "k", spawn, probe });

  assert.equal(a.started, false);
  assert.equal(b.started, false);
  assert.equal(spawn.calls.length, 0, "no spawn across repeated already-up calls");
});

test("per-agent: derives endpoint from agentId and spawns with its port/key", async () => {
  const dir = makeTempDir();
  try {
    const child = fakeChild(7777);
    const spawn = recordingSpawn(child);
    const probe = sequencedProbe([
      { available: false, reason: "daemon not running" },
      { available: true, version: "0.15.1" },
    ]);

    const ep = agentEndpoint("agent-a", { tempDir: dir });
    const result = await ensureDaemon({
      agentId: "agent-a",
      endpoint: ep,
      spawn,
      probe,
      healthTimeoutMs: 2000,
      pollMs: 10,
    });

    assert.equal(result.started, true);
    assert.equal(spawn.calls.length, 1);
    const call = spawn.calls[0];
    assert.deepEqual(call.args, ["gateway", "run", "--replace"]);
    assert.equal(call.opts.env.API_SERVER_ENABLED, "1");
    assert.equal(call.opts.env.API_SERVER_KEY, ep.key);
    assert.equal(call.opts.env.API_SERVER_PORT, String(ep.port));
    assert.equal(call.opts.env.API_SERVER_HOST, "127.0.0.1");
  } finally {
    cleanup(dir);
  }
});

test("per-agent: two agents resolve different ports + keys (no collision)", async () => {
  const dir = makeTempDir();
  try {
    const spawnA = recordingSpawn(fakeChild(1));
    const spawnB = recordingSpawn(fakeChild(2));
    const probeA = sequencedProbe([
      { available: false, reason: "down" },
      { available: true, version: "0.15.1" },
    ]);
    const probeB = sequencedProbe([
      { available: false, reason: "down" },
      { available: true, version: "0.15.1" },
    ]);

    await ensureDaemon({
      agentId: "agent-a",
      endpoint: agentEndpoint("agent-a", { tempDir: dir }),
      spawn: spawnA,
      probe: probeA,
      healthTimeoutMs: 2000,
      pollMs: 10,
    });
    await ensureDaemon({
      agentId: "agent-b",
      endpoint: agentEndpoint("agent-b", { tempDir: dir }),
      spawn: spawnB,
      probe: probeB,
      healthTimeoutMs: 2000,
      pollMs: 10,
    });

    const envA = spawnA.calls[0].opts.env;
    const envB = spawnB.calls[0].opts.env;
    assert.notEqual(envA.API_SERVER_PORT, envB.API_SERVER_PORT, "ports must differ");
    assert.notEqual(envA.API_SERVER_KEY, envB.API_SERVER_KEY, "keys must differ");
  } finally {
    cleanup(dir);
  }
});

test("per-agent: derives endpoint from agentId when none supplied", async () => {
  const dir = makeTempDir();
  try {
    const spawn = recordingSpawn(fakeChild(3));
    const probe = sequencedProbe([
      { available: false, reason: "down" },
      { available: true, version: "0.15.1" },
    ]);

    // No explicit endpoint: ensureDaemon must derive it via agentEndpoint.
    // Inject the same tempDir so the test is hermetic.
    const ep = agentEndpoint("derive-me", { tempDir: dir });
    await ensureDaemon({
      agentId: "derive-me",
      tempDir: dir,
      spawn,
      probe,
      healthTimeoutMs: 2000,
      pollMs: 10,
    });

    const env = spawn.calls[0].opts.env;
    assert.equal(env.API_SERVER_PORT, String(ep.port));
    assert.equal(env.API_SERVER_KEY, ep.key);
  } finally {
    cleanup(dir);
  }
});

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

test("ensureDaemon already-up: no spawn AND no kill-prior even if a prior pid exists", async () => {
  const dir = makeTempDir();
  try {
    writeDaemonPid("idem", 9999, dir);
    const spawn = recordingSpawn();
    const killTree = recordingKillTree();
    const probe = sequencedProbe([{ available: true, version: "0.15.1" }]);
    const result = await ensureDaemon({
      agentId: "idem",
      tempDir: dir,
      endpoint: agentEndpoint("idem", { tempDir: dir }),
      spawn,
      probe,
      killTree,
      isAlive: () => true,
    });
    assert.equal(result.started, false);
    assert.equal(spawn.calls.length, 0, "idempotent fast-path must not spawn");
    assert.equal(killTree.calls.length, 0, "idempotent fast-path must not kill-prior");
  } finally {
    cleanup(dir);
  }
});

test("ensureDaemon down + prior pid alive: kills prior, spawns, writes new pid", async () => {
  const dir = makeTempDir();
  try {
    writeDaemonPid("respawn", 4242, dir);
    const child = fakeChild(8001);
    const spawn = recordingSpawn(child);
    const killTree = recordingKillTree();
    const probe = sequencedProbe([
      { available: false, reason: "down" },
      { available: true, version: "0.15.1" },
    ]);
    const result = await ensureDaemon({
      agentId: "respawn",
      tempDir: dir,
      endpoint: agentEndpoint("respawn", { tempDir: dir }),
      spawn,
      probe,
      killTree,
      isAlive: (pid) => pid === 4242,
      healthTimeoutMs: 2000,
      pollMs: 10,
    });
    assert.equal(result.started, true);
    assert.equal(killTree.calls.length, 1, "prior daemon must be killed exactly once");
    assert.equal(killTree.calls[0], 4242, "kill-prior must target the tracked prior pid");
    assert.equal(spawn.calls.length, 1, "a fresh daemon must be spawned");
    assert.equal(readDaemonPid("respawn", dir), 8001, "new child pid must be persisted");
  } finally {
    cleanup(dir);
  }
});

test("ensureDaemon down + no prior pid: spawns, writes pid, no kill", async () => {
  const dir = makeTempDir();
  try {
    const child = fakeChild(8002);
    const spawn = recordingSpawn(child);
    const killTree = recordingKillTree();
    const probe = sequencedProbe([
      { available: false, reason: "down" },
      { available: true, version: "0.15.1" },
    ]);
    await ensureDaemon({
      agentId: "fresh",
      tempDir: dir,
      endpoint: agentEndpoint("fresh", { tempDir: dir }),
      spawn,
      probe,
      killTree,
      isAlive: () => true,
      healthTimeoutMs: 2000,
      pollMs: 10,
    });
    assert.equal(killTree.calls.length, 0, "no prior pid → no kill");
    assert.equal(spawn.calls.length, 1);
    assert.equal(readDaemonPid("fresh", dir), 8002, "new child pid persisted");
  } finally {
    cleanup(dir);
  }
});

test("ensureDaemon down + prior pid NOT alive: spawns, writes new pid, no kill (stale-pid safe)", async () => {
  const dir = makeTempDir();
  try {
    writeDaemonPid("stale", 1111, dir);
    const child = fakeChild(8003);
    const spawn = recordingSpawn(child);
    const killTree = recordingKillTree();
    const probe = sequencedProbe([
      { available: false, reason: "down" },
      { available: true, version: "0.15.1" },
    ]);
    await ensureDaemon({
      agentId: "stale",
      tempDir: dir,
      endpoint: agentEndpoint("stale", { tempDir: dir }),
      spawn,
      probe,
      killTree,
      isAlive: () => false, // prior pid is dead → must NOT signal a recycled pid
      healthTimeoutMs: 2000,
      pollMs: 10,
    });
    assert.equal(killTree.calls.length, 0, "dead prior pid must not be killed");
    assert.equal(spawn.calls.length, 1);
    assert.equal(readDaemonPid("stale", dir), 8003, "new child pid replaces the stale one");
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

test("per-agent already-up: probes the agent's baseUrl and never spawns", async () => {
  const dir = makeTempDir();
  try {
    const spawn = recordingSpawn();
    const ep = agentEndpoint("up-agent", { tempDir: dir });
    let probedWith;
    const probe = async (args) => {
      probedWith = args;
      return { available: true, version: "0.15.1" };
    };
    const result = await ensureDaemon({ agentId: "up-agent", endpoint: ep, spawn, probe });
    assert.equal(result.started, false);
    assert.equal(spawn.calls.length, 0);
    assert.equal(probedWith.baseUrl, ep.baseUrl);
    assert.equal(probedWith.key, ep.key);
  } finally {
    cleanup(dir);
  }
});
