#!/usr/bin/env node
// Regression test for the MSYS-PID marker auto-deletion bug.
//
// The bash wrappers (claude-aify, codex-aify) used to write the runtime
// marker via a short-lived `node runtime-markers.js write` CLI call and
// passed `$$` as the `pid` field. On Linux that works — $$ is a real
// long-lived kernel PID of the wrapper shell. On Git Bash for Windows,
// $$ is the MSYS shell PID, and process.kill(msys_pid, 0) fails because
// that PID does not exist in the Windows process table. isProcessAlive
// returns false, listRuntimeMarkers auto-deletes the marker, and the
// bridge can't find anything. Unifying root cause for:
//   - claude-needs-channel wake mode on Windows
//   - Codex marker auto-discovery falling through to random threads
//
// Fix: marker writes happen inside the long-lived bridge process
// (claude-channel.js, server.js), which has a real Windows PID via
// node's process.pid. The CLI form is no longer used by the wrappers.
//
// This test asserts the bridge-pattern works:
//   1. A marker written by the current process is readable back.
//   2. A marker written with a bogus caller-supplied pid gets auto-
//      deleted on the next read (current behavior — this is by design
//      for cleanup of dead markers).
//
// Run: node mcp/stdio/tests/msys-pid-bug.test.js

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

process.env.XDG_STATE_HOME = fs.mkdtempSync(path.join(os.tmpdir(), "aify-msys-test-"));

const {
  writeRuntimeMarker,
  readRuntimeMarker,
  listRuntimeMarkers,
  isProcessAlive,
} = await import("../runtime-markers.js");

const CWD = "C:/Docker/test-project";

// Sanity: an obviously fake PID must not be considered alive.
const FAKE_PID = 99999999;
assert.equal(isProcessAlive(FAKE_PID), false, "fake PID should not report alive");

// Bridge pattern: write from within this live process, don't pass a pid.
// The marker should persist because process.pid belongs to a live process.
writeRuntimeMarker("claude-code", CWD, {
  channelEnabled: true,
});
const ownMarker = readRuntimeMarker("claude-code", CWD);
assert.ok(ownMarker, "marker written by a live process must be readable back");
assert.equal(
  ownMarker.pid,
  process.pid,
  "marker pid must be the writer's real process.pid",
);
assert.equal(ownMarker.channelEnabled, true, "data fields preserved");

// Wrapper-CLI pattern (broken): if a caller overrides pid with a value
// that doesn't match a live process, the marker is considered dead and
// auto-deleted on the next read. This documents the bug behavior so we
// remember WHY the wrappers must not write markers themselves.
writeRuntimeMarker("codex", CWD, {
  appServerUrl: "ws://127.0.0.1:55555",
  pid: FAKE_PID,
});
const codexMarkers = listRuntimeMarkers("codex", CWD);
assert.equal(
  codexMarkers.length,
  0,
  "marker written with a dead pid must be auto-deleted on read (this is why wrappers must not write markers)",
);

console.log("msys-pid-bug.test.js: all assertions passed");

try {
  fs.rmSync(process.env.XDG_STATE_HOME, { recursive: true, force: true });
} catch {
  // ignore
}
