#!/usr/bin/env node
// Regression test: a Windows backslash cwd and a forward-slash cwd must
// produce the same marker key, the same marker file path, and the same
// stored cwd field. Without this, Windows Codex registrations can hit
// "Invalid request: AbsolutePathBuf deserialized without a base path"
// because the wrapper writes the marker with one slash style and the
// server reads it with the other → sha256 hashes differ → no marker found
// → live binding fails.
//
// Run:  node mcp/stdio/tests/cwd-normalization.test.js

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { tmpDir } from "./_tmpdir.js";

process.env.XDG_STATE_HOME = tmpDir("aify-comms-test-");

const {
  markerFilePath,
  writeRuntimeMarker,
  readRuntimeMarker,
  removeRuntimeMarker,
} = await import("../runtime-markers.js");

const BACKSLASH = "C:\\Users\\test\\project";
const FORWARD = "C:/Users/test/project";

// 1. Same marker path regardless of slash style.
const backPath = markerFilePath("codex", BACKSLASH, 111);
const fwdPath = markerFilePath("codex", FORWARD, 111);
assert.equal(backPath, fwdPath, "markerFilePath must return the same file for backslash and forward-slash cwd");

// 2. Writing with backslash then reading with forward slash returns the
//    same marker (this is the exact bug: wrapper writes one way, server
//    reads the other).
writeRuntimeMarker("codex", BACKSLASH, {
  appServerUrl: "ws://127.0.0.1:55555",
  channelEnabled: true,
  markerId: "backslash-write",
});

const readFwd = readRuntimeMarker("codex", FORWARD);
assert.ok(readFwd, "readRuntimeMarker with forward-slash cwd must find the marker written with backslash cwd");
assert.equal(readFwd.appServerUrl, "ws://127.0.0.1:55555");
assert.equal(readFwd.cwd, FORWARD, "marker's stored cwd field must be normalized to forward-slash form");

// 3. And the reverse: write forward, read backslash.
removeRuntimeMarker("codex", BACKSLASH);
writeRuntimeMarker("codex", FORWARD, {
  appServerUrl: "ws://127.0.0.1:66666",
  channelEnabled: true,
  markerId: "forward-write",
});

const readBack = readRuntimeMarker("codex", BACKSLASH);
assert.ok(readBack, "readRuntimeMarker with backslash cwd must find the marker written with forward-slash cwd");
assert.equal(readBack.appServerUrl, "ws://127.0.0.1:66666");

console.log("cwd-normalization.test.js: all assertions passed");

// Best-effort cleanup.
try {
  fs.rmSync(process.env.XDG_STATE_HOME, { recursive: true, force: true });
} catch {
  // ignore
}
