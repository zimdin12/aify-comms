// aify-env's AGENT column converges on the truth, whenever the truth turns up.
//
// THE OPERATOR'S RULE, 2026-08-28: "a wrapper who is auto registered should not differ from one that
// is registered later on (mid conversation)". And: "data should always be correct. we have
// communicating pieces of soft."
//
// THE SHAPE THAT FAILED. The label was sent once, in the `POST /processes` body, at spawn. That is
// correct for the path where the caller knows the answer by then and produces a permanently blank
// column for every other -- and a blank column reads as broken, not as unknown. It also made the
// display hostage to the bridge's build: a bridge started before the label existed could never send
// one, which is exactly what the operator hit, four restarts of the wrong component deep.
//
// This repo already had the rule, from an unrelated incident: "cleanup that must hold for ALL paths
// keys on the STATE". A spawn is an event and there are several; "whose process is this" is a state
// and there is one.
import assert from "node:assert/strict";
import { test } from "node:test";

import { labelsToPush, reconcileLabels } from "../label-reconciler.mjs";

const delegated = (envProcessId, agentId) => ({ envProcessId, agentId, kind: "delegated" });

test("a process whose label is already right is not written again", () => {
  // A reconciler that rewrote everything every tick would be constant writes saying nothing, and
  // would bury the one case worth noticing: a label that keeps changing.
  const pushes = labelsToPush(
    [{ id: "p1", label: "sc-architect" }],
    [delegated("p1", "sc-architect")],
  );
  assert.deepEqual(pushes, []);
});

test("a blank label learns the agent that owns the process", () => {
  // THE LIVE CASE, 2026-08-28: five processes in aify-env with `label: ""`, five terminal rows in the
  // control plane each carrying a real agent id.
  const pushes = labelsToPush(
    [{ id: "p1", label: "" }, { id: "p2", label: "" }],
    [delegated("p1", "sc-architect"), delegated("p2", "sc-coder")],
  );
  assert.deepEqual(pushes, [{ id: "p1", label: "sc-architect" }, { id: "p2", label: "sc-coder" }]);
});

test("a label that is WRONG is corrected, not just filled", () => {
  // Reuse is real: aify-env hands out p1, p2, p3 and a later process can take an id an earlier one
  // had. A reconciler that only filled blanks would leave the previous agent's name on it.
  const pushes = labelsToPush([{ id: "p1", label: "old-agent" }], [delegated("p1", "new-agent")]);
  assert.deepEqual(pushes, [{ id: "p1", label: "new-agent" }]);
});

test("an agent id that goes away clears the label rather than leaving a stale name", () => {
  // The lie that costs most is naming an agent that is not there. Blank is the honest answer.
  const pushes = labelsToPush([{ id: "p1", label: "sc-architect" }], [delegated("p1", "")]);
  assert.deepEqual(pushes, [{ id: "p1", label: "" }]);
});

test("another service's processes are left alone", () => {
  // aify-env is a shared tier. Relabelling a process this bridge did not start would be asserting
  // ownership it does not have, and the listing contains exactly those rows.
  const pushes = labelsToPush(
    [{ id: "p1", label: "" }, { id: "p9", label: "somebody-elses" }],
    [delegated("p1", "ours")],
  );
  assert.deepEqual(pushes, [{ id: "p1", label: "ours" }]);
});

test("a locally-spawned pty is not claimed", () => {
  // Only a delegated terminal has a row in aify-env. A local one has no `envProcessId`, and a `kind`
  // this module does not recognise is not one it may claim to know the owner of.
  assert.deepEqual(labelsToPush([{ id: "p1", label: "" }], [{ agentId: "a", kind: "local" }]), []);
  assert.deepEqual(labelsToPush([{ id: "p1", label: "" }], [{ agentId: "a", envProcessId: "p1" }]), []);
});

test("missing and malformed input produce no writes rather than throwing", () => {
  // This runs inside the loop that delivers work. A display label must never be able to abort it.
  for (const [processes, terminals] of [
    [undefined, undefined], [[], undefined], [undefined, []],
    [[{}], [delegated("p1", "a")]], [[{ id: "p1" }], [null]],
  ]) {
    assert.doesNotThrow(() => labelsToPush(processes, terminals));
  }
  assert.deepEqual(labelsToPush([{ id: "p1" }], [delegated("p1", "")]), [],
    "a process with no label field and an empty agent id was written for no reason");
});

// ---- the async half: it must be unable to break the loop it runs in ----------------------------

function fakeClient({ processes = [], failOn = null, listThrows = false } = {}) {
  const calls = [];
  return {
    calls,
    list: async () => { if (listThrows) throw new Error("ECONNREFUSED"); return { processes }; },
    setLabel: async (id, label) => {
      calls.push([id, label]);
      if (failOn === id) throw new Error("404 no such process");
      return { ok: true };
    },
  };
}

test("it pushes exactly the differences", async () => {
  const client = fakeClient({ processes: [{ id: "p1", label: "" }, { id: "p2", label: "right" }] });
  const result = await reconcileLabels({
    client, terminals: [delegated("p1", "left"), delegated("p2", "right")],
  });
  assert.deepEqual(client.calls, [["p1", "left"]]);
  assert.deepEqual(result, { pushed: 1, failed: 0 });
});

test("an unreachable environment is a quiet no-op, not a thrown loop", async () => {
  const client = fakeClient({ listThrows: true });
  const result = await reconcileLabels({ client, terminals: [delegated("p1", "a")] });
  assert.equal(result.skipped, "unreachable");
  assert.deepEqual(client.calls, []);
});

test("one process that has exited does not stop the others being relabelled", async () => {
  // A 404 is expected traffic here: the listing and the write are two moments, and a process can end
  // between them. Aborting the batch would leave every later process wrong until the next tick.
  const client = fakeClient({
    processes: [{ id: "p1", label: "" }, { id: "p2", label: "" }],
    failOn: "p1",
  });
  const result = await reconcileLabels({
    client, terminals: [delegated("p1", "gone"), delegated("p2", "here")],
  });
  assert.deepEqual(client.calls, [["p1", "gone"], ["p2", "here"]]);
  assert.deepEqual(result, { pushed: 1, failed: 1 });
});

test("with delegation off there is nothing to reconcile and no call is made", async () => {
  const result = await reconcileLabels({ client: null, terminals: [delegated("p1", "a")] });
  assert.equal(result.skipped, "no-client");
});

test("a bare array listing is accepted as well as {processes}", async () => {
  // The client returns whatever aify-env's /processes gives. Pinning one shape here and the other in
  // the client is how two halves of one contract start disagreeing.
  const calls = [];
  await reconcileLabels({
    client: { list: async () => [{ id: "p1", label: "" }], setLabel: async (...a) => { calls.push(a); return { ok: true }; } },
    terminals: [delegated("p1", "sc-tester")],
  });
  assert.deepEqual(calls, [["p1", "sc-tester"]]);
});
