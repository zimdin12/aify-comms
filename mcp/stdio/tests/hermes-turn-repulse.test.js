import assert from "node:assert/strict";
import { test } from "node:test";
import {
  startInFlightRepulse,
  shouldManagedHostRepulse,
  isTerminalRunStatus,
  shouldLatchComplete,
} from "../hermes-turn-repulse.js";

// --- startInFlightRepulse -------------------------------------------------
//
// DETERMINISTIC, by a manual scheduler. These four tests used real setTimeout with 25-55ms margins
// and asserted on Date.now() deltas and pulse COUNTS, so they failed roughly one run in N under the
// parallel bridge suite on a loaded machine.
//
// That was a release-gate defect, not a nuisance: this repo gates tags on suite-green, and a test
// that fails intermittently teaches everyone to re-run until green -- which is exactly how a real
// failure gets waved through. The reviewer ruled out widening the margins (still wall-clock) and
// quarantining (removes the gate), so the timer moved to the boundary instead. Same treatment that
// made terminal_diagnostics and the status engine testable.
//
// Nothing here sleeps. Ticks are driven explicitly, so the assertions can be exact counts rather
// than ">= 2 in 55ms, probably".

function manualScheduler() {
  let fn = null;
  let cleared = false;
  return {
    scheduler: {
      setInterval: (callback) => {
        fn = callback;
        return { unref() {} };
      },
      clearInterval: () => {
        cleared = true;
        fn = null;
      },
    },
    // Drive one interval and let the async tick body settle.
    async tick() {
      if (!fn) return;
      await fn();
    },
    get cleared() {
      return cleared;
    },
    get scheduled() {
      return fn !== null;
    },
  };
}

test("startInFlightRepulse pulses on each tick while in flight", async () => {
  const m = manualScheduler();
  let calls = 0;
  const stop = startInFlightRepulse({
    intervalMs: 10,
    isInFlight: () => true,
    pulse: async () => { calls += 1; },
    scheduler: m.scheduler,
  });
  await m.tick();
  await m.tick();
  await m.tick();
  stop();
  assert.strictEqual(calls, 3, "exactly one pulse per tick while in flight");
});

test("startInFlightRepulse stops pulsing the moment isInFlight goes false", async () => {
  const m = manualScheduler();
  let calls = 0;
  let inFlight = true;
  const stop = startInFlightRepulse({
    intervalMs: 10,
    isInFlight: () => inFlight,
    pulse: async () => { calls += 1; },
    scheduler: m.scheduler,
  });
  await m.tick();
  assert.strictEqual(calls, 1);
  inFlight = false;
  await m.tick();
  await m.tick();
  stop();
  assert.strictEqual(calls, 1, "no pulses after in-flight went false");
});

test("startInFlightRepulse awaits an async isInFlight (pending-promise probe)", async () => {
  const m = manualScheduler();
  let calls = 0;
  let inFlight = false;
  const stop = startInFlightRepulse({
    intervalMs: 10,
    isInFlight: async () => inFlight,
    pulse: async () => { calls += 1; },
    scheduler: m.scheduler,
  });
  await m.tick();
  assert.strictEqual(calls, 0, "async in-flight=false must not pulse");
  inFlight = true;
  await m.tick();
  await m.tick();
  stop();
  assert.strictEqual(calls, 2, "async in-flight=true must resume pulses");
});

test("startInFlightRepulse treats a throwing probe as not-in-flight", async () => {
  const m = manualScheduler();
  let calls = 0;
  const stop = startInFlightRepulse({
    intervalMs: 10,
    isInFlight: async () => { throw new Error("probe fail"); },
    pulse: async () => { calls += 1; },
    scheduler: m.scheduler,
  });
  await m.tick();
  stop();
  assert.strictEqual(calls, 0, "a failed probe must not pulse");
});

test("startInFlightRepulse swallows pulse errors (never kills the timer)", async () => {
  const m = manualScheduler();
  let ticks = 0;
  const stop = startInFlightRepulse({
    intervalMs: 10,
    isInFlight: () => true,
    pulse: async () => {
      ticks += 1;
      throw new Error("net fail");
    },
    scheduler: m.scheduler,
  });
  await m.tick();
  await m.tick();
  stop();
  assert.strictEqual(ticks, 2, "the beat must survive a throwing pulse");
});

test("stop() clears the timer and prevents further pulses", async () => {
  const m = manualScheduler();
  let calls = 0;
  const stop = startInFlightRepulse({
    intervalMs: 10,
    isInFlight: () => true,
    pulse: async () => { calls += 1; },
    scheduler: m.scheduler,
  });
  stop();
  assert.ok(m.cleared, "stop() must clear the interval");
  await m.tick();
  assert.strictEqual(calls, 0, "no pulse after stop()");
});

test("an invalid config schedules nothing at all", async () => {
  const m = manualScheduler();
  const stop = startInFlightRepulse({ intervalMs: 0, isInFlight: () => true, pulse: async () => {}, scheduler: m.scheduler });
  assert.strictEqual(typeof stop, "function");
  assert.strictEqual(m.scheduled, false, "a bad interval must not start a beat");
});

test("production uses real timers when no scheduler is injected", () => {
  // The seam must not change the default. If this ever regressed to a no-op default, every
  // production re-pulse would silently stop and hermes turns would flip to `online` mid-turn.
  const stop = startInFlightRepulse({
    intervalMs: 3_600_000,
    isInFlight: () => false,
    pulse: async () => {},
  });
  assert.strictEqual(typeof stop, "function");
  stop();
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

// --- shouldLatchComplete (the 2026-06-02 false-busy latch decision) --------

test("shouldLatchComplete latches for delivered + require_reply=0 (the false-busy fix)", () => {
  // A delivery-only message/nudge (info, reminder, 'you there?') is DONE once
  // delivered; the agent owes no tracked turn. Latch so the re-pulse stops and
  // the agent doesn't show `working` forever / block queued deliveries.
  for (const rr of [0, false, undefined, null, ""]) {
    assert.strictEqual(
      shouldLatchComplete({ status: "delivered", requireReply: rr }),
      true,
      `delivered + rr=${JSON.stringify(rr)} must latch`,
    );
  }
});

test("shouldLatchComplete KEEPS pulsing for delivered + require_reply=1 (no #172 regression)", () => {
  // A real turn the agent is working before it self-replies. Must NOT latch, or
  // a long managed-hermes turn under-shows `working`.
  for (const rr of [1, true]) {
    assert.strictEqual(
      shouldLatchComplete({ status: "delivered", requireReply: rr }),
      false,
      `delivered + rr=${JSON.stringify(rr)} must keep pulsing`,
    );
  }
});

test("shouldLatchComplete KEEPS pulsing for claimed/running regardless of require_reply", () => {
  for (const status of ["claimed", "running", "queued"]) {
    for (const rr of [0, 1, true, false]) {
      assert.strictEqual(
        shouldLatchComplete({ status, requireReply: rr }),
        false,
        `${status} + rr=${rr} must keep pulsing (real in-flight turn)`,
      );
    }
  }
});

test("shouldLatchComplete latches for every terminal status regardless of require_reply", () => {
  for (const status of ["completed", "failed", "cancelled", "stopped", "  COMPLETED  "]) {
    for (const rr of [0, 1, true, false]) {
      assert.strictEqual(
        shouldLatchComplete({ status, requireReply: rr }),
        true,
        `${status} + rr=${rr} must latch (terminal)`,
      );
    }
  }
});
