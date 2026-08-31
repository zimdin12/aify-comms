// Which owned processes get reported to an operator, and -- far more important -- which do not.
//
// THE FIXTURE IS THE OPERATOR'S FLEET on 2026-08-31: eleven Claude Code processes still running for
// ten `apg-pilot` agents that had been intentionally removed, one agent holding two of them, beside
// processes for agents that were perfectly alive.
//
// MOST OF THESE TESTS ARE ABOUT REFUSING TO NAME SOMETHING. The module reports; it does not authorise
// a stop. But a report is what an operator acts on, so a wrong name here still ends with somebody's
// working agent killed -- just with a human in between. The bar is the same.

import assert from "node:assert/strict";
import test from "node:test";

import {
  OWNER_ALIVE,
  OWNER_REMOVED,
  OWNER_UNKNOWN,
  describeDecision,
  ownerFromStatus,
  unownedProcessDecision,
} from "../unowned-process-decision.mjs";

// -- ownerFromStatus ----------------------------------------------------------------------------

test("the three codes the live service actually returns", () => {
  // Measured: apg-pilot-07 (removed) 410, comms-tech-lead (alive) 200, a fabricated id 404.
  assert.equal(ownerFromStatus(410), OWNER_REMOVED);
  assert.equal(ownerFromStatus(200), OWNER_ALIVE);
  assert.equal(ownerFromStatus(404), OWNER_UNKNOWN);
});

test("404 IS NOT REMOVAL -- this is the distinction the whole design rests on", () => {
  // An agent that has not registered yet answers 404. Treating that as removal points an operator at
  // an agent mid-spawn, which is the one outcome worse than leaving orphans running.
  assert.notEqual(ownerFromStatus(404), OWNER_REMOVED);
});

test("no other status means removed, however error-shaped it looks", () => {
  // A 403, a 500, a proxy's 502 say nothing about whether the agent exists. "Any 4xx/5xx means gone"
  // is how a service blip becomes a fleet-wide reap.
  for (const code of [0, 301, 400, 401, 403, 409, 418, 500, 502, 503, NaN, null, undefined, "410x"]) {
    assert.notEqual(ownerFromStatus(code), OWNER_REMOVED, `status ${code} was read as removal`);
  }
});

// -- unownedProcessDecision ---------------------------------------------------------------------

const REMOVED_AT = 1_700_000_000_000;
const BEFORE = REMOVED_AT - 60_000;
const AFTER = REMOVED_AT + 60_000;

const proc = (id, pid, label, startedAtMs = BEFORE) => ({ id, pid, label, startedAtMs });

// Trimmed from the real listing.
const FLEET = [
  proc("...-p2", 32456, "apg-pilot-01"),
  proc("...-p9", 239888, "apg-pilot-07"),
  proc("...-p12", 37196, "apg-pilot-07"),   // the duplicate custody
  proc("...-p15", 60260, "graph-senior-dev"),
  proc("...-p16", 47636, "comms-senior-dev"),
];
const OWNERS = {
  "apg-pilot-01": OWNER_REMOVED,
  "apg-pilot-07": OWNER_REMOVED,
  "graph-senior-dev": OWNER_ALIVE,
  "comms-senior-dev": OWNER_ALIVE,
};
const REMOVALS = { "apg-pilot-01": REMOVED_AT, "apg-pilot-07": REMOVED_AT };

test("it reports only the removed agents' processes and keeps the live ones", () => {
  const { candidates, keep } = unownedProcessDecision(FLEET, OWNERS, REMOVALS);
  assert.deepEqual(candidates.map((r) => r.pid).sort((a, b) => a - b), [32456, 37196, 239888]);
  assert.deepEqual(keep.map((r) => r.pid).sort((a, b) => a - b), [47636, 60260]);
});

test("BOTH processes of a duplicated agent are reported, not just one", () => {
  // apg-pilot-07 held two. A decision keyed on the agent rather than the process would name one and
  // leave the other, which is the state the operator was already in.
  const { candidates } = unownedProcessDecision(FLEET, OWNERS, REMOVALS);
  assert.equal(candidates.filter((r) => r.label === "apg-pilot-07").length, 2);
});

// -- the generation guard: the restore race ------------------------------------------------------

test("A PROCESS THAT STARTED AFTER THE REMOVAL IS KEPT -- it belongs to a later life", () => {
  // THE RACE THE REVIEWER FOUND, 2026-09-01. Restore DELETES the tombstone, so an agent can be
  // removed, restored and started again. A snapshot taken before that restore and acted on after it
  // reads the old tombstone and names the NEW process. A tombstone is intent; it is not proof that
  // this particular process predates it.
  const { candidates, keep } = unownedProcessDecision(
    [proc("p1", 111, "apg-pilot-01", AFTER)],
    { "apg-pilot-01": OWNER_REMOVED },
    { "apg-pilot-01": REMOVED_AT },
  );
  assert.deepEqual(candidates, []);
  assert.match(keep[0].why, /later life/);
});

test("a process started at the same millisecond as the removal is kept", () => {
  // The boundary goes to keeping. A process whose start cannot be ordered strictly before the removal
  // has not been proven to predate it, and an unproven ordering is not authority.
  const { candidates } = unownedProcessDecision(
    [proc("p1", 111, "apg-pilot-01", REMOVED_AT)],
    { "apg-pilot-01": OWNER_REMOVED },
    { "apg-pilot-01": REMOVED_AT },
  );
  assert.deepEqual(candidates, []);
});

test("a removal with NO KNOWN TIME cannot order anything, so nothing is reported", () => {
  // The tombstone exists but its epoch was not supplied. Falling back to "report it anyway" would
  // silently restore the exact behaviour the generation guard was added to remove.
  const { candidates, keep } = unownedProcessDecision(
    [proc("p1", 111, "apg-pilot-01", BEFORE)],
    { "apg-pilot-01": OWNER_REMOVED },
    {},
  );
  assert.deepEqual(candidates, []);
  assert.match(keep[0].why, /cannot be placed/);
});

test("a process with no start time cannot be ordered either", () => {
  const { candidates, keep } = unownedProcessDecision(
    [{ id: "p1", pid: 111, label: "apg-pilot-01" }],
    { "apg-pilot-01": OWNER_REMOVED },
    { "apg-pilot-01": REMOVED_AT },
  );
  assert.deepEqual(candidates, []);
  assert.match(keep[0].why, /cannot be placed/);
});

test("a non-numeric start time is not silently coerced into an ordering", () => {
  for (const started of ["", "soon", null, NaN, undefined, {}]) {
    const { candidates } = unownedProcessDecision(
      [{ id: "p1", pid: 111, label: "apg-pilot-01", startedAtMs: started }],
      { "apg-pilot-01": OWNER_REMOVED },
      { "apg-pilot-01": REMOVED_AT },
    );
    assert.deepEqual(candidates, [], `startedAtMs ${String(started)} produced a candidate`);
  }
});

test("a candidate carries the two timestamps its ordering was decided from", () => {
  // So the claim can be checked by whoever reads the report rather than taken on trust.
  const { candidates } = unownedProcessDecision(
    [proc("p1", 111, "apg-pilot-01", BEFORE)], { "apg-pilot-01": OWNER_REMOVED }, REMOVALS,
  );
  assert.equal(candidates[0].startedAt, BEFORE);
  assert.equal(candidates[0].removedAt, REMOVED_AT);
});

// -- everything not understood is kept -----------------------------------------------------------

test("AN AGENT THAT ANSWERED 404 IS KEPT -- it may not have registered yet", () => {
  const { candidates, keep } = unownedProcessDecision(
    [proc("p1", 111, "brand-new-agent")],
    { "brand-new-agent": ownerFromStatus(404) },
    {},
  );
  assert.deepEqual(candidates, []);
  assert.match(keep[0].why, /could not be established/);
});

test("an agent MISSING from the answers entirely is kept", () => {
  // The service did not answer for it at all. Absence of an answer is not an answer.
  const { candidates, keep } = unownedProcessDecision([proc("p1", 111, "who-knows")], {}, {});
  assert.deepEqual(candidates, []);
  assert.equal(keep.length, 1);
});

test("a process with NO LABEL is kept and says why -- it cannot be attributed", () => {
  // The label is the only link from a process to an agent. `gateway-orphans` reports this same
  // `(unclaimed)` case and stops there; guessing which agent owns an unlabelled process is how this
  // ends with the wrong thing killed.
  const { candidates, keep } = unownedProcessDecision([proc("p1", 111, "")], OWNERS, REMOVALS);
  assert.deepEqual(candidates, []);
  assert.match(keep[0].why, /no label/);
});

test("every input lands in exactly one list, so nothing is silently dropped", () => {
  const { candidates, keep } = unownedProcessDecision(FLEET, OWNERS, REMOVALS);
  assert.equal(candidates.length + keep.length, FLEET.length);
});

test("junk rows are skipped rather than crashing the caller", () => {
  const { candidates, keep } = unownedProcessDecision(
    [null, "nope", 7, proc("p", 1, "apg-pilot-01")], OWNERS, REMOVALS,
  );
  assert.equal(candidates.length, 1);
  assert.equal(keep.length, 0);
});

test("an empty fleet reports nothing, and junk arguments do not throw", () => {
  assert.deepEqual(unownedProcessDecision([], {}, {}), { candidates: [], keep: [] });
  assert.deepEqual(unownedProcessDecision(null, null, null), { candidates: [], keep: [] });
  assert.deepEqual(unownedProcessDecision(undefined), { candidates: [], keep: [] });
});

// -- describeDecision ----------------------------------------------------------------------------

test("it NAMES every process it reports, with pid and label", () => {
  // A report that gives a count is asking to be trusted. One that names its subjects can be argued
  // with before anybody acts on it.
  const line = describeDecision(unownedProcessDecision(FLEET, OWNERS, REMOVALS));
  for (const bit of ["apg-pilot-01 pid 32456", "apg-pilot-07 pid 239888", "apg-pilot-07 pid 37196"]) {
    assert.ok(line.includes(bit), `${bit} was not named`);
  }
});

test("THE REPORT SAYS STOPPING IS AN OPERATOR ACTION, and why", () => {
  // The reviewer's whole objection was that `tombstone + label` is not stop authority: the join is a
  // mutable, reusable display string. A report that hides that reads like a verdict, and a verdict is
  // what this module is not allowed to give.
  const line = describeDecision(unownedProcessDecision(FLEET, OWNERS, REMOVALS));
  assert.match(line, /operator action/);
  assert.match(line, /mutable label, not an identity/);
});

test("THE REPORT NAMES THE CLOCK-SKEW LIMIT TOO, which is the condition it exists under", () => {
  // `startedAtMs` is minted by aify-env's host clock, `removed_at` by aify-comms', and the
  // architecture permits separate machines. So the ordering can be wrong in either direction under
  // skew: it NARROWS the restore race, it does not close it.
  //
  // The reviewer allowed the classification to stand only on condition that its output names this
  // alongside the label limit. Dropping the sentence would leave a list that reads like proof of
  // ordering across a boundary where no such proof exists -- so this test is the condition, not a
  // wording preference.
  const line = describeDecision(unownedProcessDecision(FLEET, OWNERS, REMOVALS));
  assert.match(line, /not the same clock/);
  assert.match(line, /advisory rather than proof/);
});

test("even a candidate is worded as an appearance, not a finding", () => {
  // One clock cannot certify another's ordering, so the report must not say a process DID start
  // before the removal.
  const line = describeDecision(unownedProcessDecision(FLEET, OWNERS, REMOVALS));
  assert.match(line, /appear to have started/);
});

test("nothing to report reads as nothing to report, not as an empty list of victims", () => {
  const line = describeDecision(unownedProcessDecision(
    [proc("p", 1, "graph-senior-dev")], { "graph-senior-dev": OWNER_ALIVE }, {},
  ));
  assert.match(line, /none to report/);
});
