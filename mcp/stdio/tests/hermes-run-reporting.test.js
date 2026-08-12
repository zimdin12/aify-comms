// Unit tests for the run-state reporting helpers, executed with a recording httpCall.
//
// These are testable at all because every one of them takes `httpCall` as a PARAMETER — no client, no base
// URL, no retry policy is owned here. So a test can pass a recorder and assert exactly what would go on the
// wire, which is the interesting property: the host's existing tests exercise these through `deliverRun` and
// `runDeliveryLoop` and therefore assert the OUTCOME of a delivery, not the SHAPE of each report.
//
// What is asserted below is deliberately what those paths do not pin: the method and path of each call, the
// runtime tag every report carries, and the distinctions between delivered / failed / requeued. A run
// reported failed when it should have been requeued loses work that was never attempted — the same
// requeue-versus-fail rule the Python side has a dedicated test file for.

import assert from "node:assert/strict";
import test from "node:test";

import {
  CHANNEL_BRIDGE_PREFIX,
  channelBridgeId,
  clearTurn,
  markRunDelivered,
  markRunFailed,
  markRunRequeued,
  reportTurnBusy,
} from "../hermes-run-reporting.mjs";

/** Records every call instead of making one. */
function recorder() {
  const calls = [];
  const httpCall = async (method, path, body) => {
    calls.push({ method, path, body });
    return { ok: true };
  };
  return { calls, httpCall };
}

test("channelBridgeId namespaces the agent under the bridge prefix", () => {
  assert.equal(channelBridgeId("agent-1"), `${CHANNEL_BRIDGE_PREFIX}-agent-1`);
});

test("channelBridgeId falls back to the bare prefix for a missing agent", () => {
  // The value is used as a bridge id in a heartbeat; `hermes-managed-host-<machine>-undefined` would
  // register a bridge row that no agent owns.
  for (const value of ["", null, undefined, "   "]) {
    assert.equal(channelBridgeId(value), CHANNEL_BRIDGE_PREFIX, `unexpected for ${JSON.stringify(value)}`);
  }
});

test("CHANNEL_BRIDGE_PREFIX is a wire value carrying the machine id", () => {
  // It embeds the OLD module name (`hermes-managed-host-<machine>`) and must not be tidied to match this
  // file: it is the identity live bridges are registered under, so renaming it orphans their rows.
  assert.match(CHANNEL_BRIDGE_PREFIX, /^hermes-managed-host-/);
  assert.ok(CHANNEL_BRIDGE_PREFIX.length > "hermes-managed-host-".length, "the machine id must be appended");
});

test("markRunDelivered PATCHes the run to delivered with an EMPTY summary", () => {
  const { calls, httpCall } = recorder();
  return markRunDelivered(httpCall, { id: "run-1" }).then(() => {
    assert.equal(calls.length, 1);
    const [call] = calls;
    assert.equal(call.method, "PATCH");
    assert.equal(call.path, "/dispatch/runs/run-1");
    assert.equal(call.body.status, "delivered");
    // Deliberate: routine delivery carries no summary so the Runs audit view stays readable. A "helpful"
    // summary here is what made a delivery receipt look like an agent's reply in production.
    assert.equal(call.body.summary, "");
    assert.equal(call.body.eventType, "delivered");
    assert.ok(call.body.appendEvent, "the audit signal rides on the event, not the summary");
  });
});

test("markRunDelivered url-encodes a run id so it stays ONE path segment", async () => {
  const { calls, httpCall } = recorder();
  await markRunDelivered(httpCall, { id: "run/../1 2" });
  // My first assertion here demanded no ".." in the path and failed against correct code.
  // `encodeURIComponent` deliberately leaves `.` alone — what stops traversal is that the SLASHES become
  // %2F, so the whole id remains a single segment and `..` has no separator to act on. Asserting the absence
  // of a character rather than the property that matters is the same mistake as checking a substring is
  // missing instead of checking a quote is escaped.
  assert.equal(calls[0].path, "/dispatch/runs/run%2F..%2F1%202");
  assert.equal(calls[0].path.split("/").length, 4, "the id must not introduce extra path segments");
});

test("markRunDelivered survives a run object with no id rather than throwing", async () => {
  // It is called on a delivery path; throwing here would abandon the run mid-report.
  for (const run of [null, undefined, {}]) {
    const { calls, httpCall } = recorder();
    await markRunDelivered(httpCall, run);
    assert.equal(calls.length, 1, `expected a call for ${JSON.stringify(run)}`);
    assert.equal(calls[0].path, "/dispatch/runs/");
  }
});

test("markRunFailed DOES carry a summary — the distinction from delivered", async () => {
  const { calls, httpCall } = recorder();
  await markRunFailed(httpCall, { id: "run-2" }, "gateway unreachable");
  assert.equal(calls[0].method, "PATCH");
  assert.equal(calls[0].body.status, "failed");
  assert.match(
    JSON.stringify(calls[0].body),
    /gateway unreachable/,
    "a failure without its reason is a failure nobody can diagnose",
  );
});

test("markRunRequeued reports requeued, NOT failed", async () => {
  // The whole point of a separate function: a requeued run was never attempted, and recording it as a
  // failure loses work and misleads the operator about what went wrong.
  const { calls, httpCall } = recorder();
  await markRunRequeued(httpCall, { id: "run-3" }, "bridge superseded");
  assert.notEqual(calls[0].body.status, "failed", "a requeue must never be filed as a failure");
  assert.match(JSON.stringify(calls[0].body), /requeue/i);
});

test("reportTurnBusy heartbeats the agent with the busy flag and the bridge id", async () => {
  const { calls, httpCall } = recorder();
  await reportTurnBusy(httpCall, "agent-1", { busy: true, runId: "run-9" });
  assert.equal(calls[0].method, "POST");
  assert.equal(calls[0].path, "/agents/agent-1/heartbeat");
  assert.equal(calls[0].body.turnBusy, true);
  assert.equal(calls[0].body.turnRunId, "run-9");
  assert.equal(calls[0].body.bridgeId, channelBridgeId("agent-1"));
});

test("reportTurnBusy coerces busy to a real boolean", async () => {
  // `turnBusy` gates delivery server-side; a truthy string would read as busy forever.
  const { calls, httpCall } = recorder();
  await reportTurnBusy(httpCall, "a", { busy: "yes" });
  assert.strictEqual(calls[0].body.turnBusy, true);
  const second = recorder();
  await reportTurnBusy(second.httpCall, "a", {});
  assert.strictEqual(second.calls[0].body.turnBusy, false, "an absent busy flag must mean not busy");
});

test("clearTurn posts turn-end for the same bridge id the busy report used", async () => {
  // If these two disagreed on bridge id, the busy flag set by one would never be cleared by the other —
  // which is how an agent goes permanently deaf behind a gate that never expires.
  const { calls, httpCall } = recorder();
  await clearTurn(httpCall, "agent-1");
  assert.equal(calls[0].method, "POST");
  assert.equal(calls[0].path, "/agents/agent-1/turn-end");
  assert.equal(calls[0].body.bridgeId, channelBridgeId("agent-1"));
});

test("every report tags the runtime, so the server can tell which bridge spoke", async () => {
  const { calls, httpCall } = recorder();
  await markRunDelivered(httpCall, { id: "r" });
  await markRunFailed(httpCall, { id: "r" }, "why");
  await reportTurnBusy(httpCall, "a", { busy: false });
  await clearTurn(httpCall, "a");
  for (const call of calls) {
    const tagged = call.body.runtime ?? call.body.turnRuntime;
    assert.equal(tagged, "hermes", `${call.path} must carry the runtime tag`);
  }
});

test("an agent id with a slash cannot escape its heartbeat path", async () => {
  const { calls, httpCall } = recorder();
  await reportTurnBusy(httpCall, "a/b", { busy: true });
  assert.equal(calls[0].path, "/agents/a%2Fb/heartbeat");
});
