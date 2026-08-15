#!/usr/bin/env node
// Tests that CALL `runtime-marker-files.js` — the marker-file read side extracted from
// `reap-managed-survivors.js` in v0.5.4.
//
// `tombstonedMarkerAgentIds` and `sweepTombstonedMarkers` already had coverage that stayed with the
// reaper's own tests; what had none is `defaultListMarkerFiles` and `defaultReadMarkers`, the two
// that decide WHICH files the sweep is even looking at. A lister that misses a marker leaves a
// survivor nothing will reap; one that returns too much hands the tombstone test files it was never
// meant to judge.
//
// THE ASYMMETRY IS THE THING TO PRESERVE. Deleting a marker whose agent is alive is unrecoverable —
// the process keeps its session and no sweep can name it again — while leaving a stale marker costs
// one wasted enumeration. Every degenerate input below must therefore fail toward KEEPING, and the
// `knownAgentIds == null` short-circuit at the top of `tombstonedMarkerAgentIds` is that rule
// written into the code.
//
// THE FIXTURES ARE THE REAL FILENAMES: `aify-hermes-port-<agent>`, `aify-hermes-daemon-pid-<agent>`,
// `aify-hermes-key-<agent>`. My first draft invented a plausible `aify-runtime-*.json` convention
// and asserted against it — the code corrected me, which is what a characterization test is for.

import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  defaultListMarkerFiles,
  defaultReadMarkers,
  sweepTombstonedMarkers,
  tombstonedMarkerAgentIds,
} from "../runtime-marker-files.js";

const dir = mkdtempSync(path.join(os.tmpdir(), "aify-markers-"));
const write = (name, body) => writeFileSync(path.join(dir, name), body, "utf8");

try {
  write("aify-hermes-port-sc-coder", "8231");
  write("aify-hermes-daemon-pid-sc-coder", "4242");
  write("aify-hermes-key-sc-coder", "secret");
  write("aify-hermes-port-sc-tester", "8232");
  write("unrelated.txt", "not a marker");

  // ── defaultListMarkerFiles: GROUPED BY AGENT, which is what makes the sweep per-agent ──────
  const groups = defaultListMarkerFiles(dir);
  assert.ok(Array.isArray(groups));
  const byAgent = Object.fromEntries(groups.map((g) => [g.agentId, g.files]));
  assert.deepEqual(Object.keys(byAgent).sort(), ["sc-coder", "sc-tester"]);
  assert.equal(byAgent["sc-coder"].length, 3, "all three marker kinds for one agent group together");
  assert.equal(byAgent["sc-tester"].length, 1);
  assert.ok(
    !groups.some((g) => g.files.some((f) => f.endsWith("unrelated.txt"))),
    "a file with no known prefix is not a marker and must not join any agent's group",
  );

  // A missing directory is an empty list, not a throw: a reaper that dies while listing leaves
  // everything it had not yet reached.
  assert.doesNotThrow(() => defaultListMarkerFiles(path.join(dir, "nope")));
  assert.deepEqual(defaultListMarkerFiles(path.join(dir, "nope")), []);

  // ── defaultReadMarkers: only the kinds it claims to read ──────────────────────────────────
  const read = defaultReadMarkers(dir);
  assert.ok(Array.isArray(read));
  const kinds = new Set(read.map((m) => m.kind));
  assert.ok(kinds.has("port") && kinds.has("daemon-pid"), "it reads the two kinds it names");
  assert.ok(!kinds.has("key"), "and deliberately not the key marker — it holds a secret, not a locator");
  assert.deepEqual(defaultReadMarkers(path.join(dir, "nope")), [], "a missing directory reads as nothing");

  // ── tombstonedMarkerAgentIds: FAIL-SAFE on an unusable keyset ─────────────────────────────
  // The keyset is the server's answer to "which agents still exist". If that answer never arrived,
  // every marker looks tombstoned — which would delete the markers of every LIVE agent.
  assert.deepEqual(tombstonedMarkerAgentIds(groups, null), [], "a null keyset sweeps NOTHING");
  assert.deepEqual(tombstonedMarkerAgentIds(groups, undefined), [], "and neither does undefined");

  assert.deepEqual(tombstonedMarkerAgentIds(groups, ["sc-coder", "sc-tester"]).sort(), [],
    "every agent known → nothing tombstoned");
  assert.deepEqual(tombstonedMarkerAgentIds(groups, ["sc-coder"]).sort(), ["sc-tester"],
    "an agent the server no longer knows is tombstoned");
  assert.deepEqual(tombstonedMarkerAgentIds(groups, []).sort(), ["sc-coder", "sc-tester"],
    "an EMPTY keyset is a real answer — no agents exist — and is not the same as a missing one");
  assert.deepEqual(tombstonedMarkerAgentIds(groups, ["  sc-coder  ", ""]).sort(), ["sc-tester"],
    "ids are trimmed and blanks dropped before comparing, so whitespace cannot orphan a live agent");

  // ── sweepTombstonedMarkers: same fail-safe, at the deleting end ───────────────────────────
  const before = readdirSync(dir).length;
  for (const unusable of [null, undefined]) {
    const result = sweepTombstonedMarkers({ knownAgentIds: unusable, tempDir: dir });
    assert.ok(result && typeof result === "object", "it reports rather than throwing");
    assert.equal(readdirSync(dir).length, before,
      "an unusable keyset deletes NOTHING — the whole point of the fail-safe");
  }

  // A directory that does not exist is a no-op sweep, not a crash on boot.
  assert.doesNotThrow(() =>
    sweepTombstonedMarkers({ knownAgentIds: ["x"], tempDir: path.join(dir, "nope") }));

  // And the sweep really does delete when the answer IS usable — otherwise every assertion above
  // would hold for a function that does nothing at all.
  sweepTombstonedMarkers({ knownAgentIds: ["sc-coder"], tempDir: dir });
  const after = readdirSync(dir);
  assert.ok(!after.some((n) => n.includes("sc-tester")), "the tombstoned agent's markers are gone");
  assert.ok(after.some((n) => n.includes("sc-coder")), "the live agent's markers are untouched");
  assert.ok(after.includes("unrelated.txt"), "and a non-marker file is never a sweep target");
} finally {
  rmSync(dir, { recursive: true, force: true });
}

console.log("runtime-marker-files.test.js: all assertions passed");
