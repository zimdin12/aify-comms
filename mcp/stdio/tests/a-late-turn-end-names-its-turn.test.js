// A turn event names the turn it is about, so a late end cannot close the next turn.
//
// External review of 0.7.6 (O2). The service orders turn events by the host time they were observed
// (service/api_core/hook_event_order.py) and, since this change, clears a `/turn-end` that names a run
// only when that run's turn is the open one. Neither helps unless the bridge SENDS them. Before this, only
// the hooks stamped `firedAtUs`; the transcript, codex and resident-hermes detectors, hermes' `clearTurn`
// and the heartbeat that starts a turn sent nothing that named the turn, and hermes blanked the run id
// before its clear could read it.
//
// What is checked here is the bridge half: what goes on the wire. The service half is
// service/tests/test_a_late_turn_end_does_not_clear_the_next_turn.py.

import assert from "node:assert/strict";
import test from "node:test";

import { bridgeSources } from "./bridge-sources.mjs";
import { clearTurn, reportTurnBusy } from "../hermes-run-reporting.mjs";
import { makeInFlightProbe } from "../hermes-inflight.mjs";
import { activeTurnHeartbeatPayload, agentHeartbeatPayload } from "../turn-busy.js";
import { hostNowUs, turnEventStamp } from "../turn-event-stamp.mjs";

function recorder() {
  const calls = [];
  const httpCall = async (method, path, body) => {
    calls.push({ method, path, body });
    return { ok: true };
  };
  return { calls, httpCall };
}

function assertStamped(body, where) {
  assert.ok(Number.isSafeInteger(body.firedAtUs) && body.firedAtUs > 0, `${where}: no firedAtUs`);
  assert.equal(typeof body.machineId, "string", `${where}: no machineId`);
  assert.ok(body.machineId.length > 0, `${where}: an empty machineId`);
}

// ── every producer on the wire, found rather than listed ─────────────────────────────────────

// A POST whose path ends in /turn-start, /turn-end or /heartbeat, with the body written in place.
const TURN_POST = /httpCall\(\s*"POST",\s*`\/agents\/\$\{[^`]*\}\/(turn-start|turn-end|heartbeat)`,\s*/g;

/** The `{...}` literal starting at `from`, or null when the body is a variable. */
function literalAt(text, from) {
  if (text[from] !== "{") return null;
  let depth = 0;
  for (let i = from; i < text.length; i++) {
    if (text[i] === "{") depth++;
    else if (text[i] === "}" && --depth === 0) return text.slice(from, i + 1);
  }
  return null;
}

/** Every literal turn-event body in `sources`: [{ where, route, body }]. A heartbeat counts only when it
 *  reports a turn (`turnBusy`); a liveness beat says nothing about one. */
function turnEventBodies(sources) {
  const found = [];
  for (const [file, text] of sources) {
    for (const match of text.matchAll(TURN_POST)) {
      const body = literalAt(text, match.index + match[0].length);
      if (body === null) continue;
      if (match[1] === "heartbeat" && !/\bturnBusy\b/.test(body)) continue;
      const line = text.slice(0, match.index).split("\n").length;
      found.push({ where: `${file}:${line}`, route: match[1], body });
    }
  }
  return found;
}

const unstamped = (sites) => sites.filter((s) => !/turnEventStamp\(\)|\bfiredAtUs\b/.test(s.body));

test("every turn event the bridge posts carries the time it observed the turn", () => {
  const sites = turnEventBodies(bridgeSources());
  // POSITIVE CONTROL, so an empty scan cannot pass: the four detector posts in server.js, the two in
  // claude-turn-detector-state.mjs, the hook poster's two, hermes' clear and turn-busy report, and
  // claude-channel's turn-busy report.
  assert.ok(sites.length >= 11, `only ${sites.length} turn-event bodies found: ${sites.map((s) => s.where)}`);
  for (const route of ["turn-start", "turn-end", "heartbeat"]) {
    assert.ok(sites.some((s) => s.route === route), `no ${route} body found, so the scan is not reading them`);
  }
  assert.deepEqual(unstamped(sites).map((s) => s.where), [],
    "these turn events carry no fire time, so a late one is applied to whatever turn is open");
});

test("CONTROL: the scan reports a body that carries no stamp", () => {
  const fake = [["fake.js",
    'await httpCall("POST", `/agents/${id}/turn-end`, { bridgeId: B, source: "x" });\n'
    + 'await httpCall("POST", `/agents/${id}/turn-start`, { bridgeId: B, ...turnEventStamp() });\n'
    + 'await httpCall("POST", `/agents/${id}/heartbeat`, { bridgeId: B });\n'
    + 'await httpCall("POST", `/agents/${id}/heartbeat`, { turnBusy: true });\n']];
  const sites = turnEventBodies(fake);
  assert.deepEqual(sites.map((s) => s.route), ["turn-end", "turn-start", "heartbeat"],
    "a liveness-only heartbeat is not a turn event");
  assert.deepEqual(unstamped(sites).map((s) => s.where), ["fake.js:1", "fake.js:4"]);
});

// ── hermes: the clear names its run ──────────────────────────────────────────────────────────

test("clearTurn names the run whose turn ended, and stamps it", async () => {
  const { calls, httpCall } = recorder();
  await clearTurn(httpCall, "agent-1", { runId: "run-7" });
  assert.equal(calls[0].path, "/agents/agent-1/turn-end");
  assert.equal(calls[0].body.runId, "run-7");
  assertStamped(calls[0].body, "clearTurn");
});

test("CONTROL: a clear with no run sends no run id, so it ends whatever turn is open", async () => {
  const { calls, httpCall } = recorder();
  await clearTurn(httpCall, "agent-1");
  await clearTurn(httpCall, "agent-1", { runId: "   " });
  for (const call of calls) {
    assert.equal("runId" in call.body, false);
    assertStamped(call.body, "a runless clear");
  }
});

test("the heartbeat that starts a hermes turn is stamped", async () => {
  const { calls, httpCall } = recorder();
  await reportTurnBusy(httpCall, "agent-1", { busy: true, runId: "run-7" });
  assertStamped(calls[0].body, "reportTurnBusy");
});

test("the in-flight probe's sustained-idle clear names the run it was watching", async () => {
  // The latch blanks `inFlight.runId`; it used to do that before the clear, which then named nothing.
  const inFlight = { submittedAt: Date.now() - 60_000, completed: false, runId: "run-1" };
  const statuses = ["working", "idle"];
  let tick = 0;
  const cleared = [];
  const probe = makeInFlightProbe({
    inFlight,
    serverUrl: "http://x",
    httpCall: async () => ({ run: { status: "delivered", requireReply: true } }),
    readGatewayStatus: async () => statuses[Math.min(tick++, statuses.length - 1)],
    clearTurnImpl: async (...args) => { cleared.push(args); },
    maxWindowMs: 10 * 60_000,
    idleDebounce: 1,
  });
  assert.equal(await probe(), true, "working: still in flight");
  assert.equal(await probe(), false, "idle after working: the turn ended");
  assert.deepEqual(cleared, [["run-1"]]);
  assert.equal(inFlight.runId, "", "…and the window is closed, as before");
});

test("the in-flight probe's no-turn-started clear names the run it failed", async () => {
  const inFlight = { submittedAt: Date.now() - 60_000, completed: false, runId: "run-2" };
  const cleared = [];
  const probe = makeInFlightProbe({
    inFlight,
    serverUrl: "http://x",
    httpCall: async () => ({}),
    fetchStatus: async () => ({ status: "delivered", requireReply: true }),
    readGatewayStatus: async () => "idle",
    failRunImpl: async () => {},
    clearTurnImpl: async (...args) => { cleared.push(args); },
    startTimeoutMs: 1,
    maxWindowMs: 10 * 60_000,
  });
  assert.equal(await probe(), false);
  assert.deepEqual(cleared, [["run-2"]]);
});

test("the delivery loop hands the probe's run id to clearTurn", () => {
  // A WIRING PIN, and a location pin, stated as such: `runDeliveryLoop` builds this callback inline, and
  // driving the whole loop to a gateway idle is what the probe tests above do not need. Without it the
  // run id stops at the callback and every hermes clear is runless again.
  const [, text] = bridgeSources().find(([file]) => file === "hermes-delivery-loop.mjs");
  assert.match(text, /clearTurnImpl:\s*\((\w+)\)\s*=>\s*clearTurn\(httpCall,\s*id,\s*\{\s*runId(?::\s*\1)?\s*\}\)/);
});

// ── the shared heartbeat builders ────────────────────────────────────────────────────────────

test("a turn heartbeat carries the stamp it is given, and a liveness beat carries none", () => {
  assert.equal(agentHeartbeatPayload({ turnBusy: true, firedAtUs: 1234 }).firedAtUs, 1234);
  assert.equal(activeTurnHeartbeatPayload({ activeRun: { id: "r" }, firedAtUs: 99 }).firedAtUs, 99);
  assert.equal("firedAtUs" in agentHeartbeatPayload({ firedAtUs: 1234 }), false,
    "a beat that reports no turn has nothing to order");
  for (const bad of [0, -5, 1.5, "7", null, undefined]) {
    assert.equal("firedAtUs" in agentHeartbeatPayload({ turnBusy: true, firedAtUs: bad }), false,
      `${JSON.stringify(bad)} is not a fire time`);
  }
});

test("the stamp is integer microseconds on the host wall clock", () => {
  assert.equal(hostNowUs(() => 1_700_000_000_123.4567), 1_700_000_000_123_457);
  const before = Date.now() * 1000;
  const { firedAtUs, machineId } = turnEventStamp();
  assert.ok(Number.isSafeInteger(firedAtUs));
  assert.ok(Math.abs(firedAtUs - before) < 5_000_000, "within seconds of Date.now()");
  assert.ok(machineId.includes(":"), "the <platform>:<host> id a bridge registers with");
});
