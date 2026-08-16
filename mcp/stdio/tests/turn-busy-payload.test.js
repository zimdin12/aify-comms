#!/usr/bin/env node
// The heartbeat body that carries turn_busy — specifically, when it carries NOTHING about the turn.
//
// `agent-heartbeat.test.js` covers the two states an agent can assert (busy true, busy false) through
// a live beat. It does not cover the third, which is the one with teeth: OMITTING `turnBusy`
// entirely. Sending `turnBusy: false` tells the server the turn ended; sending no `turnBusy` key
// tells it nothing and leaves whatever it believes intact. A heartbeat that confused those two would
// clear turn_busy on every ordinary liveness beat, and turn_busy is the gate this project has had to
// bound twice — a stuck one strands queued work, a wrongly-cleared one lets a second turn start on
// top of a live one.
//
// The guard is `typeof turnBusy === "boolean"`, which is stricter than truthiness on purpose: 0, "",
// null and undefined are all "no opinion", not "not busy".
//
// `activeTurnHeartbeatPayload` had no test naming it at all. It is the wrapper the delivery loops
// use to re-pulse a running turn, and its job is to dig the run id out of whichever field the caller
// happens to have.

import assert from "node:assert/strict";

import { activeTurnHeartbeatPayload, agentHeartbeatPayload } from "../turn-busy.js";

// ── the identity fields are always present ───────────────────────────────────────────────────
{
  assert.deepEqual(agentHeartbeatPayload(), { bridgeId: "", machineId: "" },
    "a bare beat still identifies itself — the server keys on these");
  assert.deepEqual(agentHeartbeatPayload({ bridgeId: "b1", machineId: "m1" }), { bridgeId: "b1", machineId: "m1" });
  assert.deepEqual(agentHeartbeatPayload({ bridgeId: null, machineId: undefined }), { bridgeId: "", machineId: "" },
    "null/undefined become empty strings rather than travelling as JSON null");
}

// ── terminalId is present only when it has content ───────────────────────────────────────────
{
  assert.equal("terminalId" in agentHeartbeatPayload({}), false);
  assert.equal("terminalId" in agentHeartbeatPayload({ terminalId: "" }), false);
  assert.equal("terminalId" in agentHeartbeatPayload({ terminalId: "   " }), false,
    "whitespace is not a terminal id");
  assert.equal(agentHeartbeatPayload({ terminalId: "  t-9  " }).terminalId, "t-9", "and it is trimmed");
}

// ── THE OMISSION CONTRACT ────────────────────────────────────────────────────────────────────
{
  // A non-boolean means "no opinion about the turn". No turnBusy, and no turn fields either.
  for (const noOpinion of [undefined, null, 0, 1, "", "true", "false", {}, []]) {
    const body = agentHeartbeatPayload({ turnBusy: noOpinion, turnRunId: "run-1", turnRuntime: "codex" });
    assert.equal("turnBusy" in body, false, `${JSON.stringify(noOpinion)} must not assert a turn state`);
    assert.equal("turnRunId" in body, false, "and must not smuggle the run id in without it");
    assert.equal("turnRuntime" in body, false);
  }

  // Both booleans DO assert, and false is sent as false rather than omitted — that is how a turn ends.
  const busy = agentHeartbeatPayload({ turnBusy: true, turnRunId: "run-1", turnRuntime: "codex" });
  assert.equal(busy.turnBusy, true);
  assert.equal(busy.turnRunId, "run-1");
  assert.equal(busy.turnRuntime, "codex");

  const idle = agentHeartbeatPayload({ turnBusy: false });
  assert.equal(idle.turnBusy, false, "false must be PRESENT — omitting it would never end a turn");
  assert.equal("turnRunId" in idle, false, "with nothing to name, the optional fields stay out");
}

// ── the turn detail fields are optional even when busy is asserted ───────────────────────────
{
  const body = agentHeartbeatPayload({ turnBusy: true });
  assert.equal(body.turnBusy, true, "a turn can be asserted without knowing its run id");
  assert.equal("turnRunId" in body, false);
  assert.equal("turnRuntime" in body, false);

  assert.equal(agentHeartbeatPayload({ turnBusy: true, turnRunId: "  r-2  " }).turnRunId, "r-2");
  assert.equal("turnRunId" in agentHeartbeatPayload({ turnBusy: true, turnRunId: "   " }), false);
  assert.equal(agentHeartbeatPayload({ turnBusy: true, turnRuntime: 7 }).turnRuntime, "7",
    "a non-string runtime is coerced rather than dropped");
}

// ── activeTurnHeartbeatPayload ───────────────────────────────────────────────────────────────
{
  const body = activeTurnHeartbeatPayload({
    bridgeId: "b1",
    machineId: "m1",
    terminalId: "t-9",
    activeRun: { runId: "run-1", runtime: "hermes" },
  });
  assert.deepEqual(body, {
    bridgeId: "b1", machineId: "m1", terminalId: "t-9",
    turnBusy: true, turnRunId: "run-1", turnRuntime: "hermes",
  });

  // ALWAYS busy — this wrapper exists to re-pulse a turn that is running, so it can never be the
  // thing that ends one.
  assert.equal(activeTurnHeartbeatPayload().turnBusy, true, "even with no run at all");

  // A NULL `activeRun` THROWS, and that is recorded rather than hardened. The default parameter
  // fires only on `undefined`, so `{ activeRun: null }` reaches the property read. It is not
  // reachable today: the sole caller, `currentTurnHeartbeatFields`, returns an ordinary beat via
  // `if (!activeRun)` before ever getting here — and the dispatch-state serializer really does emit
  // `activeRun: null` for an idle agent, so that guard is load-bearing.
  //
  // Left as a throw deliberately. For turn_busy specifically, a loud error at the call site is
  // better than the alternative this function would otherwise produce: a heartbeat asserting a turn
  // IS busy with no run id to attribute it to. Silently pulsing an anonymous busy is how an agent
  // gets stuck working, which this project has had to fix twice.
  assert.throws(() => activeTurnHeartbeatPayload({ activeRun: null }), TypeError);

  // `runId` or `id` — the run object reaches this from two different shapes (the dispatch-state
  // serializer names it runId, a raw row names it id), and reading only one would re-pulse an
  // anonymous turn that the server cannot attribute.
  assert.equal(activeTurnHeartbeatPayload({ activeRun: { runId: "a" } }).turnRunId, "a");
  assert.equal(activeTurnHeartbeatPayload({ activeRun: { id: "b" } }).turnRunId, "b");
  assert.equal(activeTurnHeartbeatPayload({ activeRun: { runId: "a", id: "b" } }).turnRunId, "a",
    "runId is the more specific name and wins");

  // A turn with no identifiable run still pulses busy, without inventing an id.
  const anonymous = activeTurnHeartbeatPayload({ activeRun: {} });
  assert.equal(anonymous.turnBusy, true);
  assert.equal("turnRunId" in anonymous, false);
  assert.equal("turnRuntime" in anonymous, false);
}

console.log("turn-busy-payload.test.js: all assertions passed");
