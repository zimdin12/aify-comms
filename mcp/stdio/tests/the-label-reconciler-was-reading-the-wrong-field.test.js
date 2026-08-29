// One reader for aify-env's process listing, and the call site that proved why it had to be one.
//
// FOUND BY EXECUTION 2026-08-29, not by reading. `EnvClient.#request` answers
// `{ ok: true, handle: <body> }` or `{ ok: false, error }`. Three readers unwrapped that by hand:
//
//     processStillListed   `listing?.handle ?? listing`        correct
//     probeEnvTerminal     `result.handle`, after checking ok  correct
//     reconcileLabels      `listing?.processes`                ALWAYS undefined
//
// So `reconcileLabels` -- what keeps aify-env's AGENT column right when a label drifts, the column the
// operator asked for by name the same day -- read `processes` off the ENVELOPE, got undefined, fell
// through to `[]` and pushed nothing. Ever. Measured against the real shape it returned
// `{pushed: 0, failed: 0}` for a listing that plainly needed one push, while the pure `labelsToPush`
// returned that push for the same data.
//
// A GREEN HELPER AND A CALL SITE WIRED TO NOTHING. This repo has a rule about that shape, written
// after shipping an interrupt feature that could never fire with six green tests, all six of which
// tested the pure builder. `labelsToPush` had tests. Nothing drove `reconcileLabels` with what
// `EnvClient` actually returns.
//
// It hid because the SPAWN path sets the label directly, so the common case worked and only drift
// repair was dead -- which is the half nobody watches.
import assert from "node:assert/strict";
import { test } from "node:test";

import { envListing } from "../env-listing.mjs";
import { labelsToPush, reconcileLabels } from "../label-reconciler.mjs";
import { processStillListed } from "../delegated-exit.mjs";

/** Exactly what `EnvClient.list()` resolves to. */
const envelope = (processes) => ({ ok: true, handle: { processes } });

function recordingClient(listing) {
  const pushed = [];
  return {
    pushed,
    list: async () => listing,
    setLabel: async (id, label) => { pushed.push({ id, label }); return { ok: true }; },
  };
}

const TERMINALS = [{ kind: "delegated", envProcessId: "abc123-p1", agentId: "sc-coder" }];

// ---- the call site, which is the part that was broken ---------------------------------------------

test("THE DEFECT: a label is pushed for what EnvClient ACTUALLY returns", async () => {
  const client = recordingClient(envelope([{ id: "abc123-p1", label: "" }]));
  const result = await reconcileLabels({ client, terminals: TERMINALS });
  assert.equal(result.pushed, 1, "the reconciler read the envelope as if it were the body and pushed "
    + "nothing, which is what it did on every tick for as long as it existed");
  assert.deepEqual(client.pushed, [{ id: "abc123-p1", label: "sc-coder" }]);
});

test("the pure decision was always right, which is why nothing caught it", () => {
  // The control that explains the shape of the bug: given the DATA, the helper is correct. The fault
  // was entirely in what the caller handed it, and a test of the helper can never see that.
  assert.deepEqual(
    labelsToPush([{ id: "abc123-p1", label: "" }], TERMINALS),
    [{ id: "abc123-p1", label: "sc-coder" }],
  );
});

test("A REFUSAL IS NOT AN EMPTY LISTING", async () => {
  // Both used to return `{pushed: 0, failed: 0}` -- an environment that never answered, reported as an
  // environment with nothing to do. `processStillListed` already calls that collapse "an absence of
  // signal read as a positive fact".
  const refused = await reconcileLabels({
    client: recordingClient({ ok: false, error: "aify-env answered 503" }), terminals: TERMINALS,
  });
  assert.equal(refused.skipped, "refused");

  const empty = await reconcileLabels({ client: recordingClient(envelope([])), terminals: TERMINALS });
  assert.equal(empty.skipped, undefined, "an environment that answered and owns nothing is not a "
    + "failure to reach it");
  assert.equal(empty.pushed, 0);
});

test("an unreadable body says so rather than reporting a quiet zero", async () => {
  const result = await reconcileLabels({
    client: recordingClient({ ok: true, handle: { something: "else" } }), terminals: TERMINALS,
  });
  assert.equal(result.skipped, "unreadable");
});

test("a label that is already right is not pushed again", async () => {
  // The reconciler's whole reason for pushing differences only: rewriting every label every tick
  // would be N writes a second to say nothing, and would hide the one case worth seeing.
  const client = recordingClient(envelope([{ id: "abc123-p1", label: "sc-coder" }]));
  const result = await reconcileLabels({ client, terminals: TERMINALS });
  assert.equal(result.pushed, 0);
  assert.deepEqual(client.pushed, []);
});

// ---- the shared reader ----------------------------------------------------------------------------

test("the reader takes the envelope, a bare body, or a bare array", () => {
  const processes = [{ id: "abc123-p1" }];
  assert.deepEqual(envListing({ ok: true, handle: { processes } }).processes, processes);
  assert.deepEqual(envListing({ processes }).processes, processes);
  assert.deepEqual(envListing(processes).processes, processes);
});

test("null processes and an empty array are different answers", () => {
  // The distinction every consumer branches on. `[]` is "aify-env answered and owns nothing";
  // `null` is "there was no readable listing", and they lead to opposite decisions.
  assert.deepEqual(envListing({ ok: true, handle: { processes: [] } }), { processes: [], refused: false });
  assert.deepEqual(envListing({ ok: false, error: "boom" }), { processes: null, refused: true });
  assert.deepEqual(envListing({ ok: true, handle: null }), { processes: null, refused: false });
  assert.deepEqual(envListing(undefined), { processes: null, refused: false });
});

test("the other two consumers still read the same listing the same way", async () => {
  // The point of extracting it. All three now agree by construction rather than by three people
  // remembering, and this asserts the two that were CORRECT did not regress in the move.
  const client = { list: async () => envelope([{ id: "abc123-p1" }]) };
  assert.equal(await processStillListed(client, "abc123-p1"), true);
  assert.equal(await processStillListed(client, "def456-p1"), false);
  assert.equal(await processStillListed({ list: async () => ({ ok: false }) }, "abc123-p1"), null,
    "a refusal must stay `null`, because `false` there would report a live process dead");
});
