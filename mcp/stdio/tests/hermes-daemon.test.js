#!/usr/bin/env node
// Unit tests for ensureDaemon — the idempotent "one api_server daemon up"
// helper. All spawn/probe are INJECTED: no real hermes process is launched
// and no real network is touched.
// Contract: docs/superpowers/specs/2026-05-30-hermes-apiserver-contract.md.

import assert from "node:assert/strict";
import { test } from "node:test";
import { ensureDaemon } from "../hermes-daemon.js";

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
