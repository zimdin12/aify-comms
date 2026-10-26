// The bridge's poll intervals, and the guards under them.
//
// Extracted from server.js in v0.5.4, where they were only ever exercised by starting a bridge. The hermes
// turn-poll floors left with the bridge's turn detector (review of bd829e7b); the delivery loop keeps its own.
//
// RE-IMPORTED PER CASE. Each constant reads `process.env` once, at module load, so the only way to test
// a value is to set the environment and load the module again — a query string busts the ESM cache, and
// this module imports nothing, so nothing stale can be pulled in behind it.
//
// EVERY VARIABLE IS SEALED and the seal is asserted. A leaked `AIFY_DISPATCH_POLL_MS` would change how
// the rest of this process behaves, and one of this repo's recorded incidents is a test that read the
// operator's live environment.

import assert from "node:assert/strict";
import test from "node:test";

const SEALED = [
  "AIFY_SESSION_HEARTBEAT_MS",
  "AIFY_DISPATCH_POLL_MS",
];

let seq = 0;

/** Load the module fresh with exactly `vars` set, restoring the environment afterwards. */
async function withEnv(vars) {
  const saved = new Map(SEALED.map((k) => [k, process.env[k]]));
  try {
    for (const k of SEALED) delete process.env[k];
    Object.assign(process.env, vars);
    return await import(`../poll-intervals.mjs?case=${seq += 1}`);
  } finally {
    for (const [k, v] of saved) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  }
}

test("the seal restores every variable it touches", async () => {
  // Asserted first, because every case below sets environment variables the bridge itself reads.
  const before = SEALED.map((k) => process.env[k]);
  await withEnv({ AIFY_DISPATCH_POLL_MS: "leaked" });
  assert.deepEqual(SEALED.map((k) => process.env[k]), before);
});

test("the DEFAULTS are what an unconfigured bridge runs with", async () => {
  // These are the numbers in production for almost every operator, so they are worth pinning as fact:
  // a silent change to any of them alters load on the service for the whole fleet.
  const m = await withEnv({});
  assert.equal(m.__HEARTBEAT_MS, 60000);
  assert.equal(m.DISPATCH_POLL_MS, 3000);
});

test("an operator's override is honoured when it is sane", async () => {
  const m = await withEnv({
    AIFY_SESSION_HEARTBEAT_MS: "30000",
    AIFY_DISPATCH_POLL_MS: "1500",
  });
  assert.equal(m.__HEARTBEAT_MS, 30000);
  assert.equal(m.DISPATCH_POLL_MS, 1500);
});

test("the heartbeat rejects a JUNK value and falls back, because of its trailing `|| 60000`", async () => {
  // `Number("abc")` is NaN and NaN is falsy, so the second `||` catches it. That guard is the only
  // reason a typo'd heartbeat interval does not become a NaN timer.
  for (const junk of ["abc", "", "   ", "12px"]) {
    const m = await withEnv({ AIFY_SESSION_HEARTBEAT_MS: junk });
    assert.equal(m.__HEARTBEAT_MS, 60000, JSON.stringify(junk));
  }
});

test("DISPATCH_POLL_MS HAS NO FLOOR AND NO NaN GUARD — pinned as an asymmetry, not endorsed", async () => {
  // The others are protected and this one is not. `Number("abc")` reaches `setInterval` as NaN,
  // which browsers and Node both treat as ~0 — a dispatch loop with no delay at all. A tiny positive
  // value passes through just as freely.
  //
  // Asserted as it BEHAVES rather than quietly fixed: adding a floor changes claim cadence for every
  // bridge, which is a tuning decision rather than a tidy-up. This test is what makes the difference
  // visible if anyone weighs it.
  const junk = await withEnv({ AIFY_DISPATCH_POLL_MS: "abc" });
  assert.ok(Number.isNaN(junk.DISPATCH_POLL_MS), "junk reaches setInterval as NaN");

  const tiny = await withEnv({ AIFY_DISPATCH_POLL_MS: "1" });
  assert.equal(tiny.DISPATCH_POLL_MS, 1, "no floor is applied");

  const negative = await withEnv({ AIFY_DISPATCH_POLL_MS: "-1" });
  assert.equal(negative.DISPATCH_POLL_MS, -1);
});
