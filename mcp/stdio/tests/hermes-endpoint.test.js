#!/usr/bin/env node
// Unit tests for the hermes session-id marker: a placeholder is never persisted, and a poison marker already
// on disk reads as absent. (The port, key and URL markers left with the gateway start in 0.9.10.) Tests inject a
// temp dir and clean it up so nothing touches the real os.tmpdir().

import "./_sealed-temp.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { tmpDir } from "./_tmpdir.js";
import {
  writeSessionIdMarker,
  readSessionIdMarker,
  isUsableSessionId,
} from "../hermes-endpoint.js";

// Make a throwaway temp dir for the per-agent key files.
function makeTempDir() {
  return tmpDir("hermes-endpoint-test-");
}
function cleanup(dir) {
  try {
    fs.rmSync(dir, { recursive: true, force: true });
  } catch {
    /* best-effort */
  }
}

test("isUsableSessionId: accepts real ids, rejects placeholders/empty", () => {
  assert.equal(isUsableSessionId("20260604_050351_21531a"), true);
  assert.equal(isUsableSessionId("aify-sc-tester"), true);
  assert.equal(isUsableSessionId("8b821120"), true);
  assert.equal(isUsableSessionId("${HERMES_SESSION_ID}"), false, "unexpanded placeholder must be rejected");
  assert.equal(isUsableSessionId(""), false);
  assert.equal(isUsableSessionId("   "), false);
  assert.equal(isUsableSessionId("has space"), false);
});

test("writeSessionIdMarker REFUSES to persist a ${...} placeholder", () => {
  const dir = makeTempDir();
  try {
    assert.equal(writeSessionIdMarker("ag", "${HERMES_SESSION_ID}", { tempDir: dir }), false);
    assert.ok(!fs.existsSync(path.join(dir, "aify-hermes-session-ag")), "no poison marker file written");
    // a real id IS persisted + read back
    assert.equal(writeSessionIdMarker("ag", "20260604_050351_21531a", { tempDir: dir }), true);
    assert.equal(readSessionIdMarker("ag", { tempDir: dir }), "20260604_050351_21531a");
  } finally {
    cleanup(dir);
  }
});

test("readSessionIdMarker treats a pre-existing poison marker as ABSENT", () => {
  const dir = makeTempDir();
  try {
    // Simulate a marker poisoned before the write-guard existed.
    fs.writeFileSync(path.join(dir, "aify-hermes-session-ag"), "${HERMES_SESSION_ID}");
    assert.equal(readSessionIdMarker("ag", { tempDir: dir }), "", "poison marker must read as empty → fall through to fresh/active_list");
  } finally {
    cleanup(dir);
  }
});
