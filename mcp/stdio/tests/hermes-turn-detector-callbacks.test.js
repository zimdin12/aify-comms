// The hermes gateway turn-detector callbacks — CALLED, for the first time.
//
// Three of the eleven never-called functions in `hermes-delivery-loop.mjs`, and each one is the fix for
// a named incident that this suite has never been able to reproduce, because firing one needed a live
// gateway and a live service:
//
//   * the turn_run_id race that deadlocked the reply reminders (2026-07-10 review);
//   * the post-turn status flap, where hermes' own background work re-fired `/turn-start` after the
//     dispatched turn had ended (2026-07-10 review F1);
//   * the gate that scopes the detector to dispatched turns at all.
//
// WHY A REGRESSION HERE WOULD BE INVISIBLE. All three are best-effort (`.catch(() => {})`), they run on
// a ~9s timer inside a loop that never returns, and their only observable effect is a heartbeat field
// on another process. A broken one does not throw, does not log, and shows up days later as "the agent
// says working but is idle" — which is precisely how both incidents were reported.
//
// The transport is a parameter, so every call is captured in an array and no server is involved.

import assert from "node:assert/strict";
import test from "node:test";

const { buildGatewayTurnCallbacks } = await import("../hermes-turn-detector-callbacks.mjs");

function harness(inFlightOverrides = {}) {
  const calls = [];
  const httpCall = async (method, endpoint, body) => {
    calls.push({ method, endpoint, body });
    return {};
  };
  const inFlight = {
    runId: "run-42",
    dispatchTurnOpen: true,
    completed: false,
    observedWorking: false,
    ...inFlightOverrides,
  };
  return { calls, inFlight, ...buildGatewayTurnCallbacks({ inFlight, httpCall, id: "hermes-agent" }) };
}

test("postTurnStart threads the OPEN run id — the field whose loss deadlocked reply reminders", async () => {
  const { calls, postTurnStart } = harness();
  await postTurnStart();

  const beat = calls.find((c) => c.endpoint.includes("/heartbeat"));
  assert.ok(beat, `no heartbeat was sent; saw ${JSON.stringify(calls.map((c) => c.endpoint))}`);
  assert.equal(beat.body.turnBusy, true);
  assert.equal(beat.body.turnRunId, "run-42",
    "the busy beat carried an EMPTY run id. The server writes `turn_run_id = excluded.turn_run_id` on "
    + "every busy beat, so an empty one overwrites the real linkage — the run can no longer be matched "
    + "to its turn and the reply reminders deadlock. This is the 2026-07-10 race, verbatim.");
});

test("postTurnStart records that the gateway was observed working", async () => {
  const { inFlight, postTurnStart } = harness({ observedWorking: false });
  await postTurnStart();
  assert.equal(inFlight.observedWorking, true,
    "the re-pulse machinery reads this to tell a turn that ran from one that never started");
});

test("postTurnEnd REVOKES the dispatched-turn credit, so background work cannot re-fire working", async () => {
  // `observedWorking: true` is REQUIRED for this path, and getting that wrong is how I wrote this test
  // the first time. The guard is `dispatchTurnOpen !== true || observedWorking === true`: an OPEN
  // dispatched turn only ends once the gateway was actually seen working. Ending one that never
  // visibly started would revoke the credit for a turn still about to run.
  const { calls, inFlight, postTurnEnd } = harness({ dispatchTurnOpen: true, observedWorking: true });
  await postTurnEnd();

  assert.equal(inFlight.dispatchTurnOpen, false,
    "the turn is still marked open, so a later gateway `working` from hermes' own post-turn "
    + "background work re-fires /turn-start — the status flap this callback exists to stop");
  assert.equal(inFlight.completed, true,
    "the re-pulse probe window is still open, so the slow 45s pulse keeps re-asserting turn_busy "
    + "after the turn ended");
  assert.ok(calls.some((c) => c.endpoint.includes("/turn-end")),
    `the authoritative turn-end was never posted; saw ${JSON.stringify(calls.map((c) => c.endpoint))}`);
});

test("postTurnEnd does NOTHING for an open turn the gateway was never seen working on", async () => {
  // ANTI-VACUITY and a real property: every assertion above would pass if this callback always fired.
  // This is the case the guard exists for — a dispatched turn is open but the gateway has not yet been
  // observed working, so the turn has not started and must not be ended.
  const { calls, inFlight, postTurnEnd } = harness({ dispatchTurnOpen: true, observedWorking: false });
  await postTurnEnd();
  assert.equal(calls.length, 0,
    `a turn-end fired for a turn that had not started; sent ${JSON.stringify(calls.map((c) => c.endpoint))}`);
  assert.equal(inFlight.dispatchTurnOpen, true,
    "the credit was revoked for a turn that is still about to run");
});

test("postTurnEnd DOES apply when no dispatched turn is open — ordinary idle detection", async () => {
  const { calls, postTurnEnd } = harness({ dispatchTurnOpen: false, observedWorking: false });
  await postTurnEnd();
  assert.ok(calls.some((c) => c.endpoint.includes("/turn-end")),
    "with no dispatched turn open the detector's idle turn-end must still clear turn_busy, or an "
    + "agent that went idle outside a dispatch stays `working` until the 120s server backstop");
});

test("shouldFireTurnStart gates the detector to DISPATCHED turns only", () => {
  assert.equal(harness({ dispatchTurnOpen: true }).shouldFireTurnStart(), true);
  assert.equal(harness({ dispatchTurnOpen: false }).shouldFireTurnStart(), false,
    "an ungated detector re-fires `working` for hermes' post-turn background activity, which is the "
    + "flap an operator sees as an agent that never goes idle");
});

test("shouldFireTurnStart is strict about the flag, not merely truthy", () => {
  // `=== true` rather than a truthy check, and that is deliberate: the field is undefined before any
  // delivery, and a truthy test on a half-initialised inFlight would read as "no turn open" by luck
  // rather than by rule. Pinned so a later simplification to `!!inFlight.dispatchTurnOpen` is a
  // decision somebody makes on purpose.
  assert.equal(harness({ dispatchTurnOpen: undefined }).shouldFireTurnStart(), false);
  assert.equal(harness({ dispatchTurnOpen: "yes" }).shouldFireTurnStart(), false,
    "a non-boolean must not open the gate");
  assert.equal(harness({ dispatchTurnOpen: 1 }).shouldFireTurnStart(), false);
});

test("every callback swallows a transport failure rather than killing the delivery loop", async () => {
  const inFlight = { runId: "r", dispatchTurnOpen: true, completed: false };
  const exploding = buildGatewayTurnCallbacks({
    inFlight,
    httpCall: async () => { throw new Error("service unreachable"); },
    id: "hermes-agent",
  });
  await assert.doesNotReject(async () => {
    await exploding.postTurnStart();
    await exploding.postTurnEnd();
  }, "a callback propagated a transport failure; the delivery loop would die on a transient outage");
});
