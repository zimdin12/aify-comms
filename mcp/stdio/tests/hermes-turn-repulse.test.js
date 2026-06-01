import assert from "node:assert/strict";
import { test } from "node:test";
import {
  startInFlightRepulse,
  shouldManagedHostRepulse,
  isTerminalRunStatus,
} from "../hermes-turn-repulse.js";

// --- startInFlightRepulse -------------------------------------------------

test("startInFlightRepulse pulses every interval while in flight", async () => {
  const calls = [];
  let inFlight = true;
  const stop = startInFlightRepulse({
    intervalMs: 10,
    isInFlight: () => inFlight,
    pulse: async () => calls.push(Date.now()),
  });
  await new Promise((r) => setTimeout(r, 55));
  stop();
  assert.ok(calls.length >= 2, `expected >=2 pulses in 55ms@10ms; got ${calls.length}`);
});

test("startInFlightRepulse stops pulsing the moment isInFlight goes false", async () => {
  const calls = [];
  let inFlight = true;
  const stop = startInFlightRepulse({
    intervalMs: 10,
    isInFlight: () => inFlight,
    pulse: async () => calls.push(1),
  });
  await new Promise((r) => setTimeout(r, 25));
  inFlight = false;
  const at = calls.length;
  await new Promise((r) => setTimeout(r, 40));
  stop();
  assert.strictEqual(calls.length, at, `no pulses after in-flight=false; got ${calls.length - at} extra`);
});

test("startInFlightRepulse awaits an async isInFlight (pending-promise probe)", async () => {
  const calls = [];
  let inFlight = false;
  const stop = startInFlightRepulse({
    intervalMs: 10,
    isInFlight: async () => inFlight,
    pulse: async () => calls.push(1),
  });
  await new Promise((r) => setTimeout(r, 35));
  assert.strictEqual(calls.length, 0, "async in-flight=false must not pulse");
  inFlight = true;
  await new Promise((r) => setTimeout(r, 35));
  stop();
  assert.ok(calls.length >= 2, `async in-flight=true must resume pulses; got ${calls.length}`);
});

test("startInFlightRepulse swallows pulse errors (never kills the timer)", async () => {
  let ticks = 0;
  const stop = startInFlightRepulse({
    intervalMs: 10,
    isInFlight: () => true,
    pulse: async () => {
      ticks++;
      throw new Error("net fail");
    },
  });
  await new Promise((r) => setTimeout(r, 35));
  stop();
  assert.ok(ticks >= 2, `timer must keep firing despite pulse throwing; ticks=${ticks}`);
});

test("startInFlightRepulse is a no-op with missing/invalid params", () => {
  assert.strictEqual(typeof startInFlightRepulse({}), "function");
  startInFlightRepulse({})(); // must not throw
  startInFlightRepulse({ intervalMs: 0, isInFlight: () => true, pulse: () => {} })();
  startInFlightRepulse({ intervalMs: 10, isInFlight: "no", pulse: () => {} })();
});

// --- shouldManagedHostRepulse (pure decision) -----------------------------

test("shouldManagedHostRepulse is false before any submit", () => {
  assert.strictEqual(shouldManagedHostRepulse({ submittedAt: 0, now: 1000 }), false);
});

test("shouldManagedHostRepulse is true inside the open window", () => {
  const t0 = 100_000;
  assert.strictEqual(
    shouldManagedHostRepulse({ submittedAt: t0, now: t0 + 60_000, maxWindowMs: 600_000 }),
    true,
  );
});

test("shouldManagedHostRepulse is false past the bounded window (anti-stuck guard)", () => {
  const t0 = 100_000;
  assert.strictEqual(
    shouldManagedHostRepulse({ submittedAt: t0, now: t0 + 600_001, maxWindowMs: 600_000 }),
    false,
  );
});

test("shouldManagedHostRepulse short-circuits the moment completion is observed", () => {
  const t0 = 100_000;
  // completed:true stops the window EVEN deep inside an otherwise-open window —
  // this is the #3 fix: an observed turn-end must beat the bounded window.
  assert.strictEqual(
    shouldManagedHostRepulse({ submittedAt: t0, now: t0 + 10_000, maxWindowMs: 600_000, completed: true }),
    false,
  );
});

test("shouldManagedHostRepulse: completed:false within window still re-pulses (no #172 regression)", () => {
  const t0 = 100_000;
  // A long, NOT-yet-completed turn well inside the window must keep re-pulsing
  // so a >120s managed-hermes turn keeps showing `working`.
  assert.strictEqual(
    shouldManagedHostRepulse({ submittedAt: t0, now: t0 + 300_000, maxWindowMs: 600_000, completed: false }),
    true,
  );
});

test("shouldManagedHostRepulse: completed:false past window stops (existing anti-stuck guard preserved)", () => {
  const t0 = 100_000;
  assert.strictEqual(
    shouldManagedHostRepulse({ submittedAt: t0, now: t0 + 600_001, maxWindowMs: 600_000, completed: false }),
    false,
  );
});

test("shouldManagedHostRepulse guards clock skew (now before submit)", () => {
  assert.strictEqual(
    shouldManagedHostRepulse({ submittedAt: 100_000, now: 90_000, maxWindowMs: 600_000 }),
    false,
  );
});

test("shouldManagedHostRepulse is anchored on submit state, never on derived status", () => {
  // Sanity: with no submittedAt, no amount of 'now' makes it pulse. This is the
  // property that prevents the 2026-05-23 feedback loop — the decision reads
  // only the bridge-owned submit timestamp, not the server's status.
  for (const now of [0, 1, 1_000_000, Date.now()]) {
    assert.strictEqual(shouldManagedHostRepulse({ submittedAt: 0, now }), false);
  }
});

// --- isTerminalRunStatus (true turn-end discriminator) --------------------

test("isTerminalRunStatus is true for completed/failed/cancelled/stopped (case/space-insensitive)", () => {
  for (const s of ["completed", "failed", "cancelled", "stopped", "COMPLETED", "  Failed  "]) {
    assert.strictEqual(isTerminalRunStatus(s), true, `expected terminal: '${s}'`);
  }
});

test("isTerminalRunStatus is false for in-flight / non-terminal statuses", () => {
  // CRITICAL (#172 safety): 'delivered' is NOT terminal — the managed turn is
  // only just STARTING at delivered. 'claimed'/'running'/'queued' are mid-turn.
  // Treating any of these as completion would stop the re-pulse early and
  // under-show `working` on a long turn (the #172 regression).
  for (const s of ["delivered", "claimed", "running", "queued", "", null, undefined, "weird"]) {
    assert.strictEqual(isTerminalRunStatus(s), false, `expected NON-terminal: '${s}'`);
  }
});
