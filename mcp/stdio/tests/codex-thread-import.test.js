#!/usr/bin/env node
// Unit test for managed Codex thread import. A dashboard-managed Codex
// home is separate from the user's normal CODEX_HOME, so a resident thread
// can legitimately exist in ~/.codex while managed resume initially reports
// "no rollout found". The adapter must copy that native rollout instead of
// forcing a fresh context.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const { findCodexThreadFiles, importCodexThreadRollout } = await import("../runtimes.js");

const base = fs.mkdtempSync(path.join(os.tmpdir(), "aify-codex-thread-import-"));
try {
  const threadId = "019d9e26-071a-7521-8f7c-108789102c1b";
  const sourceHome = path.join(base, "source-home");
  const targetHome = path.join(base, "target-home");
  const rolloutDir = path.join(sourceHome, "sessions", "2026", "04", "18");
  const snapshotDir = path.join(sourceHome, "shell_snapshots");
  const rolloutFile = path.join(
    rolloutDir,
    `rollout-2026-04-18T01-13-05-${threadId}.jsonl`,
  );
  const snapshotFile = path.join(snapshotDir, `${threadId}.123456.sh`);
  fs.mkdirSync(rolloutDir, { recursive: true });
  fs.mkdirSync(snapshotDir, { recursive: true });
  fs.writeFileSync(rolloutFile, "{\"type\":\"thread\"}\n");
  fs.writeFileSync(snapshotFile, "cd /tmp\n");

  const found = findCodexThreadFiles({ threadId, sourceHome });
  assert.deepEqual(found.rollouts, [rolloutFile]);
  assert.deepEqual(found.shellSnapshots, [snapshotFile]);

  const imported = importCodexThreadRollout({ threadId, sourceHome, targetHome });
  assert.equal(imported.imported, true);
  assert.equal(imported.sourceHome, sourceHome);
  assert.deepEqual(imported.rollouts, [
    path.join("sessions", "2026", "04", "18", `rollout-2026-04-18T01-13-05-${threadId}.jsonl`),
  ]);
  assert.deepEqual(imported.shellSnapshots, [
    path.join("shell_snapshots", `${threadId}.123456.sh`),
  ]);

  assert.equal(
    fs.readFileSync(path.join(targetHome, imported.rollouts[0]), "utf8"),
    "{\"type\":\"thread\"}\n",
  );
  assert.equal(
    fs.readFileSync(path.join(targetHome, imported.shellSnapshots[0]), "utf8"),
    "cd /tmp\n",
  );

  const missing = importCodexThreadRollout({
    threadId: "missing-thread",
    sourceHome,
    targetHome,
  });
  assert.equal(missing.imported, false);
  assert.deepEqual(missing.rollouts, []);
} finally {
  fs.rmSync(base, { recursive: true, force: true });
}

console.log("codex-thread-import.test.js: all assertions passed");
