#!/usr/bin/env node
// The counters that decide whether a failing claim is logged once or a thousand times.
//
// `spawnClaimFailureCount` and `spawnClaimLastLogAt` were named by no test. They are the whole memory
// of the spawn claim loop: the loop polls continuously, so without them a service restart produces a
// log line every second until someone notices, and with them broken in the other direction an outage
// produces nothing at all.
//
// COUNTED AND RESET ARE BOTH TESTED. A counter that increments but never resets turns the next
// unrelated failure into "47 consecutive", which reads as a much worse incident than it is — and the
// recovery line, the only positive signal the loop ever emits, is gated on that same count.
//
// THE RECEIPT MARKER IS HERE TOO because it is the same kind of value: a constant whose only job is
// to be recognised elsewhere. `AIFY_COMMS_RECEIPT_TEXT` is what a claude console prints when a
// message arrives, and the codex frame wraps the same text — if the two disagreed, one runtime's
// receipts would stop being recognised by whatever matches on them.

import assert from "node:assert/strict";
import test from "node:test";

import {
  AIFY_COMMS_RECEIPT_TEXT,
  claudeAifyReceiptLine,
  codexAifyReceiptFrame,
} from "../aify-console-markers.js";
import {
  CONTROL_CLAIM_FAILURES,
  noteControlClaimFailure,
  noteControlClaimSuccess,
  noteSpawnClaimFailure,
  noteSpawnClaimSuccess,
  spawnClaimFailureCount,
  spawnClaimLastLogAt,
} from "../claim-failure-tracker.mjs";

/** Run fn with console.error/debug captured, so a logging test never pollutes the run's output. */
function captureLogs(fn) {
  const lines = [];
  const realError = console.error;
  const realDebug = console.debug;
  console.error = (...args) => lines.push(args.join(" "));
  console.debug = (...args) => lines.push(args.join(" "));
  try {
    fn();
  } finally {
    console.error = realError;
    console.debug = realDebug;
  }
  return lines;
}

/** The module-level counters are LIVE BINDINGS — re-read per call, never snapshotted. */
async function counters() {
  const m = await import("../claim-failure-tracker.mjs");
  return { count: m.spawnClaimFailureCount, lastLogAt: m.spawnClaimLastLogAt };
}

test.beforeEach(() => {
  captureLogs(() => noteSpawnClaimSuccess());
  CONTROL_CLAIM_FAILURES.clear();
});

test("the counters start at zero and are numbers", async () => {
  assert.deepEqual(await counters(), { count: 0, lastLogAt: 0 });
  assert.equal(typeof spawnClaimFailureCount, "number");
  assert.equal(typeof spawnClaimLastLogAt, "number");
});

test("each failure increments the count", async () => {
  captureLogs(() => {
    noteSpawnClaimFailure(new Error("connect ECONNREFUSED"));
    noteSpawnClaimFailure(new Error("connect ECONNREFUSED"));
    noteSpawnClaimFailure(new Error("connect ECONNREFUSED"));
  });
  assert.equal((await counters()).count, 3);
});

test("a success RESETS both counters, not just the count", async () => {
  // `lastLogAt` surviving a recovery would suppress the first warning of the NEXT outage — the one
  // that matters most, because it is the one that says something just broke.
  captureLogs(() => {
    noteSpawnClaimFailure(new Error("boom"));
    noteSpawnClaimFailure(new Error("boom"));
    noteSpawnClaimSuccess();
  });
  assert.deepEqual(await counters(), { count: 0, lastLogAt: 0 });
});

test("a success with no prior failure logs nothing", async () => {
  // TWO GUARDS HOLD THIS, and each absorbs the other: the tracker checks `count > 0`, the policy
  // checks `count >= warnAfter`. Removing either alone changes no outcome — which reads as a
  // vacuous assertion and is not one. Removing BOTH makes this test fail, which is the historical
  // shape it is really about: a loop that announces a recovery from nothing every poll.
  const lines = captureLogs(() => noteSpawnClaimSuccess());
  assert.deepEqual(lines, [], "a healthy loop must be silent");
  assert.equal((await counters()).count, 0);
});

test("a recovery says how many failures preceded it", () => {
  const lines = captureLogs(() => {
    for (let i = 0; i < 3; i += 1) noteSpawnClaimFailure(new Error("boom"));
    noteSpawnClaimSuccess();
  });
  const recovery = lines.filter((l) => l.includes("recovered"));
  assert.equal(recovery.length, 1, "exactly one recovery line");
  assert.match(recovery[0], /spawn claim recovered after 3 failure\(s\)/);
});

test("a SINGLE failure is silent — one blip is not an outage", () => {
  // Deliberate, and the opposite of what I assumed when writing this file: the first failure only
  // emits a debug line, gated on AIFY_DEBUG. The loop polls continuously, so a service that
  // restarts in under a second would otherwise announce itself every time.
  const lines = captureLogs(() => noteSpawnClaimFailure(new Error("connect ECONNREFUSED")));
  assert.deepEqual(lines.filter((l) => l.includes("spawn claim failed")), []);
});

test("the warning arrives when the failure is SUSTAINED, and names the cause", () => {
  const lines = captureLogs(() => {
    for (let i = 0; i < 3; i += 1) noteSpawnClaimFailure(new Error("connect ECONNREFUSED"));
  });
  const warnings = lines.filter((l) => l.includes("spawn claim failed"));
  assert.equal(warnings.length, 1, "exactly one warning for the run of failures");
  assert.match(warnings[0], /\(3 consecutive\)/, "the count is what makes it an outage, not a blip");
  assert.match(warnings[0], /connect ECONNREFUSED/, "the cause has to survive into the log");
  assert.match(warnings[0], /keep retrying/, "and the operator needs to know it is not fatal");
});

test("a storm of failures does not produce a storm of warnings", () => {
  // The whole reason `lastLogAt` exists. The loop polls continuously; 200 failures inside one
  // window must not be 200 lines, or the log stops being readable exactly when it is needed.
  const lines = captureLogs(() => {
    for (let i = 0; i < 200; i += 1) noteSpawnClaimFailure(new Error("boom"));
  });
  const warnings = lines.filter((l) => l.includes("spawn claim failed"));
  assert.ok(warnings.length < 10, `${warnings.length} warnings for 200 consecutive failures`);
  assert.ok(warnings.length >= 1, "…but it must not go completely silent either");
});

test("the CONTROL tracker is keyed by label, so one loop cannot inflate the other", () => {
  captureLogs(() => {
    noteControlClaimFailure("environment controls", new Error("boom"));
    noteControlClaimFailure("environment controls", new Error("boom"));
    noteControlClaimFailure("terminal controls", new Error("boom"));
  });
  assert.equal(CONTROL_CLAIM_FAILURES.get("environment controls").count, 2);
  assert.equal(CONTROL_CLAIM_FAILURES.get("terminal controls").count, 1);
});

test("a control loop's recovery clears ONLY its own entry", () => {
  captureLogs(() => {
    noteControlClaimFailure("environment controls", new Error("boom"));
    noteControlClaimFailure("terminal controls", new Error("boom"));
    noteControlClaimSuccess("terminal controls");
  });
  assert.equal(CONTROL_CLAIM_FAILURES.has("terminal controls"), false, "recovered entry not cleared");
  assert.equal(CONTROL_CLAIM_FAILURES.get("environment controls").count, 1,
    "a healthy loop's recovery wiped the failing loop's memory");
});

// ── the receipt marker ───────────────────────────────────────────────────────────────────────

test("the claude receipt line IS the shared constant", () => {
  assert.equal(claudeAifyReceiptLine(), AIFY_COMMS_RECEIPT_TEXT);
  assert.ok(AIFY_COMMS_RECEIPT_TEXT.trim().length > 0);
});

test("the codex frame carries the same text, wrapped for a terminal", () => {
  // Two runtimes, one recognisable string. If the codex frame spelled it differently, anything
  // matching on the marker would stop seeing codex receipts and nothing would report an error.
  const frame = codexAifyReceiptFrame();
  assert.ok(frame.includes(AIFY_COMMS_RECEIPT_TEXT), "the codex frame lost the shared marker");
  assert.ok(frame.startsWith("\r\n") && frame.endsWith("\r\n"),
    "it is written into a live PTY, so it must not join the line already on screen");
});
