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
