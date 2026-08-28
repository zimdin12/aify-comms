// A delegated terminal is not declared dead on evidence that never showed a death.
//
// THE ORPHAN FACTORY, traced on the operator's host 2026-08-28 from the terminal's own event log:
//
//     18:24:52  the process starts under aify-env as p1, pid 155844
//     18:32:44  terminal_output ... and the row is marked `stopped`
//     18:32:45  terminal_consistency_repaired
//     18:34:25  reconciled_managed_orphan_worker
//
// The operator killed aify-env at 18:32. Every delegated output stream ended at once, the bridge
// reported each terminal as exited, and the processes SURVIVED -- aify-env's shutdown deliberately
// leaves children it cannot confirm and keeps the owned record so the next instance can reap them.
// aify-env came back, correctly re-owned pid 155844, and the control plane has said `stopped` about a
// live, owned process ever since. Verified while writing this: the owned record's `owner` is 40636,
// which is the CURRENT aify-env's pid, so the reaper is right to skip it. The divergence is one-sided.
//
// THE DISTINCTION WAS CARRIED AND THROWN AWAY. `env-client.mjs` says it where the stream ends: "The
// stream closed with no exit frame. aify-env sends one and then ends, so reaching here means the
// environment went away rather than the process finishing." It then called the same `finish(null)` a
// real exit uses, so the caller could not tell, and finalised either way.
import assert from "node:assert/strict";
import { test } from "node:test";

import { delegatedExitVerdict, processStillListed } from "../delegated-exit.mjs";
import { reattachLostStreams, settleDelegatedExit } from "../delegated-stream.mjs";

test("an observed exit frame is an exit", () => {
  // The only positive evidence of a death there is: aify-env watched it happen and said so.
  const verdict = delegatedExitVerdict({ observedExitFrame: true });
  assert.equal(verdict.kind, "exited");
  assert.equal(verdict.finalise, true);
});

test("a stream that ends while the process is STILL LISTED is not an exit", () => {
  // THE OPERATOR'S CASE. The stream broke, not the process.
  const verdict = delegatedExitVerdict({ observedExitFrame: false, stillListed: true });
  assert.equal(verdict.kind, "alive");
  assert.equal(verdict.finalise, false, "a live, owned process would have been marked stopped");
  assert.match(verdict.reason, /still owns/);
});

test("a stream that ends and the process is GONE is an exit", () => {
  // The control for the case above. Holding every terminal open would trade an orphaned process for a
  // row that never closes, and the reconcilers would be healing forever.
  const verdict = delegatedExitVerdict({ observedExitFrame: false, stillListed: false });
  assert.equal(verdict.kind, "exited");
  assert.equal(verdict.finalise, true);
  assert.match(verdict.reason, /no longer lists/);
});

test("NOBODY COULD SAY is not a death", () => {
  // The asymmetry that decides this. A stale `attached` row is what terminal_consistency.py,
  // terminal_runs.py and managed_workers.py exist to heal; NOTHING collects a process the control
  // plane has already called stopped.
  const verdict = delegatedExitVerdict({ observedExitFrame: false, stillListed: null });
  assert.equal(verdict.kind, "unknown");
  assert.equal(verdict.finalise, false);
});

test("no arguments does not finalise", () => {
  // A default that closes the terminal is the orphan factory with a shorter call.
  assert.equal(delegatedExitVerdict().finalise, false);
});

test("every verdict says why", () => {
  for (const input of [
    { observedExitFrame: true },
    { observedExitFrame: false, stillListed: true },
    { observedExitFrame: false, stillListed: false },
    { observedExitFrame: false, stillListed: null },
  ]) {
    const { reason } = delegatedExitVerdict(input);
    assert.ok(reason && reason.length > 10, `no reason for ${JSON.stringify(input)}`);
  }
});

// ---- asking the environment ---------------------------------------------------------------------

const clientListing = (value) => ({ list: async () => value });

test("a listed process reads as still there", async () => {
  const client = clientListing({ ok: true, handle: { processes: [{ id: "p1" }, { id: "p2" }] } });
  assert.equal(await processStillListed(client, "p1"), true);
});

test("an absent process reads as gone", async () => {
  const client = clientListing({ ok: true, handle: { processes: [{ id: "p2" }] } });
  assert.equal(await processStillListed(client, "p1"), false);
});

test("an EMPTY listing is a real answer, not an absent one", async () => {
  // aify-env alive and owning nothing is a definite no. Reading it as "could not ask" would hold every
  // terminal open after a clean environment restart.
  const client = clientListing({ ok: true, handle: { processes: [] } });
  assert.equal(await processStillListed(client, "p1"), false);
});

test("a REFUSAL is not an empty listing", async () => {
  // `{ ok: false }` means the request did not land. Reading it as "not listed" would say the process
  // is gone about an environment that never answered -- the exact collapse this fixes.
  assert.equal(await processStillListed({ list: async () => ({ ok: false, error: "unreachable" }) }, "p1"), null);
});

test("a refusal is refused on its OK FLAG, not on happening to carry no body", async () => {
  // WHY THIS CASE EXISTS. A mutation deleting the `ok === false` guard SURVIVED: today's refusal
  // from `EnvClient.#request` is `{ ok: false, error }` with no `handle`, so the shape fallback
  // below returns null anyway and the guard looks redundant. It is not redundant, it is the
  // CONTRACT -- `#request` already parses the response body before it checks the status, and a
  // refusal that gained a body would start reading as an authoritative empty listing.
  const refusedWithBody = { ok: false, error: "aify-env answered 503", handle: { processes: [] } };
  assert.equal(
    await processStillListed({ list: async () => refusedWithBody }, "p1"), null,
    "a refused request was read as an authoritative listing, so a live process would read as gone",
  );
});

test("a client that throws, or is missing, cannot say", async () => {
  assert.equal(await processStillListed({ list: async () => { throw new Error("boom"); } }, "p1"), null);
  assert.equal(await processStillListed(null, "p1"), null);
  assert.equal(await processStillListed({}, "p1"), null);
});

test("no process id cannot be answered", async () => {
  // A delegated terminal with no `envProcessId` is one we never had a handle for; claiming it is gone
  // would finalise a terminal on the strength of our own missing bookkeeping.
  const client = clientListing({ ok: true, handle: { processes: [] } });
  assert.equal(await processStillListed(client, ""), null);
  assert.equal(await processStillListed(client, undefined), null);
});

test("a bare array listing is accepted as well as {processes}", async () => {
  assert.equal(await processStillListed(clientListing([{ id: "p1" }]), "p1"), true);
});

test("a listing that is not a list cannot say", async () => {
  // Not "no processes". A body this reader does not understand is one it must not draw a conclusion
  // from, and `false` here would mark a live terminal stopped.
  for (const value of [{ ok: true, handle: {} }, { ok: true, handle: null }, "healthy", null, undefined]) {
    assert.equal(await processStillListed(clientListing(value), "p1"), null, JSON.stringify(value));
  }
});


// ---- the policy, called directly -----------------------------------------------------------
//
// These take the manager as a PARAMETER, which is what moving them out of the class bought: the
// decision can be driven without standing up a TerminalProcessManager. The manager-driven versions
// live in delegated-terminal-controls.test.js and are the ones that prove the wiring; these prove
// the branches, including the ones a fake client makes awkward to reach through the class.

function fakeManager({ listing = null, handled = [], written = [] } = {}) {
  return {
    envDelegation: { isEnabled: () => true, client: { list: async () => listing } },
    terminals: new Map(),
    handled,
    written,
    async _handleExit(id, state, detail) { handled.push([id, detail]); },
    async onOutput(id, text) { written.push(text); },
    async _attachDelegatedStream() { return () => {}; },
  };
}

test("settleDelegatedExit finalises an observed exit without asking anyone", async () => {
  // There is nothing to verify: aify-env watched it happen. Asking anyway would spend a round trip
  // per exit on the one path that needs none.
  let asked = false;
  const manager = fakeManager();
  manager.envDelegation.client.list = async () => { asked = true; return null; };
  const verdict = await settleDelegatedExit(manager, "t1", { envProcessId: "p1" }, {
    code: 0, signal: "", meta: { observedExitFrame: true },
  });
  assert.equal(verdict.kind, "exited");
  assert.equal(manager.handled.length, 1);
  assert.equal(asked, false, "an observed exit still cost a listing call");
});

test("settleDelegatedExit holds a terminal whose process is still listed, and says so", async () => {
  const manager = fakeManager({ listing: { ok: true, handle: { processes: [{ id: "p1" }] } } });
  const state = { envProcessId: "p1" };
  const verdict = await settleDelegatedExit(manager, "t1", state, { code: null, signal: "", meta: {} });
  assert.equal(verdict.finalise, false);
  assert.deepEqual(manager.handled, [], "a live process was finalised");
  assert.equal(state.streamLost, "alive", "the terminal was not marked for re-attachment");
  assert.ok(
    manager.written.some((text) => text.includes("lost the output stream")),
    "the console went quiet instead of being told",
  );
});

test("a console that throws does not change the decision", async () => {
  // The notice is a courtesy; the hold is the correctness. A broken console must not turn a held
  // terminal into a finalised one.
  const manager = fakeManager({ listing: { ok: true, handle: { processes: [{ id: "p1" }] } } });
  manager.onOutput = async () => { throw new Error("no console"); };
  const state = { envProcessId: "p1" };
  await settleDelegatedExit(manager, "t1", state, { code: null, signal: "", meta: {} });
  assert.deepEqual(manager.handled, []);
  assert.equal(state.streamLost, "alive");
});

test("reattachLostStreams skips a terminal with no process id", async () => {
  // A delegated terminal we never got a handle for. Re-subscribing to an empty id would ask aify-env
  // about nothing and read the refusal as a failure to re-attach, for ever.
  const manager = fakeManager();
  manager.terminals.set("t1", { streamLost: "alive", envProcessId: "" });
  assert.deepEqual(await reattachLostStreams(manager), { reattached: [], stillLost: [], finalised: [] });
});

test("reattachLostStreams skips a terminal that was already finalised", async () => {
  // Belt and braces against a race: a terminal finalised between the hold and the tick must not be
  // brought back to life by the reconciler.
  const manager = fakeManager();
  manager.terminals.set("t1", { streamLost: "alive", envProcessId: "p1", finalized: true });
  assert.deepEqual(await reattachLostStreams(manager), { reattached: [], stillLost: [], finalised: [] });
});

test("reattachLostStreams treats a throwing subscribe as still lost", async () => {
  const manager = fakeManager();
  manager._attachDelegatedStream = async () => { throw new Error("ECONNREFUSED"); };
  manager.terminals.set("t1", { streamLost: "alive", envProcessId: "p1" });
  assert.deepEqual(await reattachLostStreams(manager), { reattached: [], stillLost: ["t1"], finalised: [] });
});


test("a held terminal whose process DIED while we were blind is finally reported", async () => {
  // THE HOLE IN MY OWN FIX, found by checking a claim instead of asserting it. Two commits ago I
  // wrote that holding is safe because "a stale `attached` row is what the reconcilers exist to
  // heal". For a DELEGATED terminal that is false: `listOwnedSessions` excludes them by design --
  // correctly, their pid is not on this host -- so `reportDeadOwnedTerminals` never sees one, and
  // nothing else reports it dead. A process that ended during an aify-env outage would have been
  // held `attached` for ever.
  const manager = fakeManager({ listing: { ok: true, handle: { processes: [] } } });
  manager._attachDelegatedStream = async () => null;   // the process is gone: nothing to subscribe to
  const state = { streamLost: "alive", envProcessId: "p1" };
  manager.terminals.set("t1", state);

  const result = await reattachLostStreams(manager);
  assert.deepEqual(result.finalised, ["t1"], "a process aify-env no longer lists was held for ever");
  assert.deepEqual(result.stillLost, []);
  assert.equal(manager.handled.length, 1, "the exit never reached the manager");
});

test("an environment that is merely DOWN keeps the terminal held", async () => {
  // The control, and the whole reason the two cases must be told apart: aify-env comes back, a dead
  // process does not. A refusal here reads as `null` -- no answer -- and holding is the safe way to
  // be unsure.
  const manager = fakeManager({ listing: { ok: false, error: "aify-env unreachable" } });
  manager._attachDelegatedStream = async () => null;
  manager.terminals.set("t1", { streamLost: "alive", envProcessId: "p1" });

  const result = await reattachLostStreams(manager);
  assert.deepEqual(result.stillLost, ["t1"]);
  assert.deepEqual(result.finalised, [], "a terminal was finalised because the environment was down");
  assert.equal(manager.handled.length, 0);
});

test("a re-attach that SUCCEEDS never asks whether the process is gone", async () => {
  // The listing call is only for the failure branch. Asking on every successful re-attach would
  // spend a round trip to answer a question the successful subscription already answered.
  let asked = false;
  const manager = fakeManager();
  manager.envDelegation.client.list = async () => { asked = true; return null; };
  manager.terminals.set("t1", { streamLost: "alive", envProcessId: "p1" });
  const result = await reattachLostStreams(manager);
  assert.deepEqual(result.reattached, ["t1"]);
  assert.equal(asked, false);
});
