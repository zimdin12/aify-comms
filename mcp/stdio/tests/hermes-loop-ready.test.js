#!/usr/bin/env node
// Unit tests for the managed-hermes delivery-loop READY MARKER
// (hermes-loop-ready.js).
//
// The marker `aify-hermes-loop-ready-<agent>` is written by the delivery loop
// AFTER it is a LIVE CLAIMER (gateway ok + heartbeat started + one successful
// /dispatch/claim round-trip). The hermes-aify wrapper health-gates on it before
// exec'ing the visible TUI (Task 1.5), so a TUI that can't receive work never
// shows. Mirrors the pid-file helpers in hermes-daemon.js: a file under
// os.tmpdir(), freshness judged by mtime, cleared on teardown.

import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  writeLoopReady,
  loopReadyFresh,
  clearLoopReady,
  loopReadyFile,
} from "../hermes-loop-ready.js";

function mkTmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "aify-loop-ready-test-"));
}

test("loopReadyFile: lives under the given dir with the sanitized agent name", () => {
  const dir = mkTmp();
  try {
    const f = loopReadyFile("sc coder!", dir);
    assert.equal(path.dirname(f), dir);
    assert.equal(path.basename(f), "aify-hermes-loop-ready-sc-coder");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("writeLoopReady creates aify-hermes-loop-ready-<agent>", () => {
  const dir = mkTmp();
  try {
    const ok = writeLoopReady("sc-coder", dir);
    assert.equal(ok, true);
    const f = path.join(dir, "aify-hermes-loop-ready-sc-coder");
    assert.ok(fs.existsSync(f), "marker file must exist after writeLoopReady");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("loopReadyFresh is true only within the mtime window", () => {
  const dir = mkTmp();
  try {
    writeLoopReady("sc-coder", dir);
    // Fresh immediately after write.
    assert.equal(loopReadyFresh("sc-coder", dir, 60_000), true, "just-written marker is fresh");

    // Backdate the mtime well past the window.
    const f = path.join(dir, "aify-hermes-loop-ready-sc-coder");
    const old = new Date(Date.now() - 5 * 60_000);
    fs.utimesSync(f, old, old);
    assert.equal(loopReadyFresh("sc-coder", dir, 60_000), false, "stale marker is not fresh");

    // A missing marker is not fresh.
    assert.equal(loopReadyFresh("nobody", dir, 60_000), false, "missing marker is not fresh");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("writeLoopReady refreshes the mtime on a re-write (claim keeps it live)", () => {
  const dir = mkTmp();
  try {
    writeLoopReady("sc-coder", dir);
    const f = path.join(dir, "aify-hermes-loop-ready-sc-coder");
    const old = new Date(Date.now() - 5 * 60_000);
    fs.utimesSync(f, old, old);
    assert.equal(loopReadyFresh("sc-coder", dir, 60_000), false, "backdated marker is stale");
    // Re-write refreshes mtime -> fresh again.
    writeLoopReady("sc-coder", dir);
    assert.equal(loopReadyFresh("sc-coder", dir, 60_000), true, "re-write refreshes freshness");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("clearLoopReady removes the marker (idempotent)", () => {
  const dir = mkTmp();
  try {
    writeLoopReady("sc-coder", dir);
    const f = path.join(dir, "aify-hermes-loop-ready-sc-coder");
    assert.ok(fs.existsSync(f));
    assert.equal(clearLoopReady("sc-coder", dir), true);
    assert.ok(!fs.existsSync(f), "marker removed");
    // Idempotent: clearing again does not throw.
    assert.doesNotThrow(() => clearLoopReady("sc-coder", dir));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("writeLoopReady: empty agentId is a no-op false (never crashes)", () => {
  const dir = mkTmp();
  try {
    assert.equal(writeLoopReady("", dir), false);
    assert.equal(loopReadyFresh("", dir, 60_000), false);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
