// What the Claude channel records about a dispatch it delivered — CALLED, for the first time.
//
// Three of the eleven functions in `claude-channel.js` that the coverage census reports as never
// called. They were unreachable for the same structural reason as the dispatch-loop callbacks: they sit
// beside a `pollLoop` that never returns, in a module whose only entry point starts a sidecar.
//
// THE PROPERTY WORTH THE TEST is the one line that decides between `delivered` and `completed`. A
// require-reply run must stay `delivered` until the agent's explicit reply closes it — that is the
// signal the dashboard lights "working" from, and the reason a resident require_reply run used to
// auto-complete on delivery with nothing showing that the agent still owed an answer. Getting it
// backwards is invisible in every other suite.
//
// NO SERVER HERE. The transport is a parameter, so the calls are captured in an array. That is the
// point of injecting it — and it also means these tests cannot accidentally reach a live service, the
// failure mode that once registered six agents into the operator's production registry.

import assert from "node:assert/strict";
import test from "node:test";

const { isChannelRun, makeDispatchReceipts } = await import("../channel-dispatch-receipts.mjs");

function recorder() {
  const calls = [];
  const httpCall = async (method, endpoint, body) => {
    calls.push({ method, endpoint, body });
    return {};
  };
  return { calls, ...makeDispatchReceipts({ httpCall }) };
}

test("isChannelRun recognises the channel execution mode, and nothing else", () => {
  assert.equal(isChannelRun({ executionMode: "channel" }), true);
  assert.equal(isChannelRun({ executionMode: "CHANNEL" }), true, "the check is case-insensitive");
  assert.equal(isChannelRun({ executionMode: "  channel  " }), true, "…and trims");
  assert.equal(isChannelRun({ executionMode: "managed" }), false);
  assert.equal(isChannelRun({ executionMode: "resident" }), false);
  assert.equal(isChannelRun({}), false, "a run with no mode is not a channel run");
  assert.equal(isChannelRun(null), false, "and neither is nothing at all");
});

test("a REQUIRE-REPLY run stays `delivered`, because the agent still owes an answer", async () => {
  const { calls, markDispatchDelivered } = recorder();
  await markDispatchDelivered({ id: "run-1", requireReply: true, executionMode: "channel" });

  assert.equal(calls.length, 1, "exactly one PATCH per delivery");
  const [call] = calls;
  assert.equal(call.method, "PATCH");
  assert.equal(call.endpoint, "/dispatch/runs/run-1");
  assert.equal(call.body.status, "delivered",
    "a require_reply run marked `completed` on delivery closes a contract the agent never answered — "
    + "the dashboard then shows nothing owing and the sender waits forever");
  assert.equal(call.body.eventType, "delivered");
});

test("a run with NO reply contract is completed on delivery", async () => {
  const { calls, markDispatchDelivered } = recorder();
  await markDispatchDelivered({ id: "run-2", requireReply: false, executionMode: "channel" });
  assert.equal(calls[0].body.status, "completed",
    "holding a run open when nothing is owed leaves a false `working` on the roster");
});

test("the receipt says WHICH path delivered it", async () => {
  // Not cosmetic: the wording of this exact receipt once claimed "Delivered to Claude resident session"
  // for a MANAGED agent, and that string became the leading hypothesis for a restart bug — it gated a
  // whole workstream before anyone checked it. A receipt that names the wrong path is a trap.
  const channel = recorder();
  await channel.markDispatchDelivered({ id: "r", requireReply: true, executionMode: "channel" });
  assert.match(channel.calls[0].body.appendEvent, /Delivered to Claude channel bridge/);

  const resident = recorder();
  await resident.markDispatchDelivered({ id: "r", requireReply: true, executionMode: "resident" });
  assert.match(resident.calls[0].body.appendEvent, /Delivered and completed by channel bridge/);
  // `assert.notMatch` does not exist on node:assert/strict in this Node — asserted directly rather
  // than reaching for a helper that silently is not there.
  assert.ok(!/resident session/.test(resident.calls[0].body.appendEvent),
    "the receipt must not claim a session mode it cannot know");
});

test("a routine delivery carries NO summary, so the Runs audit stays readable", async () => {
  const { calls, markDispatchDelivered } = recorder();
  await markDispatchDelivered({ id: "run-3", requireReply: true, executionMode: "channel" });
  assert.equal(calls[0].body.summary, "",
    "a summary on every routine delivery buries the failures, which are the rows worth reading");
});

test("a failure records the reason, not just the failure", async () => {
  const { calls, markDispatchDeliveryFailed } = recorder();
  await markDispatchDeliveryFailed("run-4", new Error("the pipe closed"));
  const [call] = calls;
  assert.equal(call.endpoint, "/dispatch/runs/run-4");
  assert.equal(call.body.status, "failed");
  assert.equal(call.body.error, "the pipe closed",
    "a failed run whose error is empty tells the sender nothing about why");
  assert.match(call.body.appendEvent, /Claude channel delivery failed: the pipe closed/);
  assert.equal(call.body.eventType, "failed");
});

test("a non-Error rejection still produces a readable reason", async () => {
  // Runtimes reject with strings and with objects. `String(error)` is what keeps the receipt useful
  // when the thing thrown was never an Error.
  const { calls, markDispatchDeliveryFailed } = recorder();
  await markDispatchDeliveryFailed("run-5", "socket hang up");
  assert.equal(calls[0].body.error, "socket hang up");
});

test("both writers report the runtime, so a run can be attributed later", async () => {
  const ok = recorder();
  await ok.markDispatchDelivered({ id: "a", requireReply: false, executionMode: "channel" });
  const bad = recorder();
  await bad.markDispatchDeliveryFailed("b", new Error("x"));
  assert.equal(ok.calls[0].body.runtime, "claude-code");
  assert.equal(bad.calls[0].body.runtime, "claude-code");
});
