// Which owned processes get reported to an operator, and -- far more important -- which do not.
//
// THE FIXTURE IS THE OPERATOR'S FLEET on 2026-08-31: eleven Claude Code processes still running for
// ten `apg-pilot` agents that had been intentionally removed, one agent holding two of them, beside
// processes for agents that were perfectly alive. What those agents had bound BEFORE the delete is not
// part of the fixture and is not knowable now -- the agents -> agent_sessions -> terminal_sessions
// cascade means present absence cannot distinguish never-created from deleted-with-the-agent.
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
  processRowProblem,
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
  assert.match(keep[0].why, /not proven to have started strictly before/);
});

test("a process started at the same millisecond as the removal is kept, AND SAYS WHY", () => {
  // The boundary goes to keeping. A process whose start cannot be ordered strictly before the removal
  // has not been proven to predate it, and an unproven ordering is not authority.
  //
  // ASSERTING THE REASON, not just the empty list. An empty `candidates` is also what a broken guard
  // produces, so the two are indistinguishable unless the kept row states the rule it was kept by.
  // EQUAL IS NOT AFTER: the wording must not claim the process started after the removal, which is a
  // stronger and different fact. Across two clocks an equal reading is not even evidence of
  // simultaneity, only of resolution.
  const { candidates, keep } = unownedProcessDecision(
    [proc("p1", 111, "apg-pilot-01", REMOVED_AT)],
    { "apg-pilot-01": OWNER_REMOVED },
    { "apg-pilot-01": REMOVED_AT },
  );
  assert.deepEqual(candidates, []);
  assert.match(keep[0].why, /not proven to have started strictly before/);
  assert.doesNotMatch(keep[0].why, /started after/);
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
  // NECESSARY, NOT SUFFICIENT, and it has now failed to catch two separate defects: a row conserved
  // into the WRONG population still conserves, and an unreadable CONTAINER has no rows on either side
  // so the arithmetic is perfect and vacuous. The population tests are what actually hold the boundary.
  const { candidates, keep, invalid } = unownedProcessDecision(FLEET, OWNERS, REMOVALS);
  assert.equal(candidates.length + keep.length + invalid.length, FLEET.length);
});

test("THE REVIEWER'S OWN HOSTILE INPUT, verbatim -- every row of it is invalid", () => {
  // RUN AGAINST THE SHIPPED COMMIT AND IT RETURNED `invalid: []`. The array, the empty object and the
  // Date were classified as unlabelled `keep`, and the fourth row became a CANDIDATE printed as
  // "gone pid null" -- a thing an operator might stop, with no identity to stop it by.
  //
  // The guard was `!entry || typeof entry !== "object"`, which rejects null and primitives and nothing
  // else. And the conservation test PASSED throughout, because conserving a row into the wrong
  // population still conserves it. A total that adds up is not a partition that is correct.
  const hostile = [[], {}, new Date(0), { id: "p", pid: null, label: "gone", startedAtMs: 1 }];
  const { candidates, keep, invalid } = unownedProcessDecision(hostile, OWNERS, REMOVALS);
  assert.deepEqual(candidates, []);
  assert.deepEqual(keep, []);
  assert.equal(invalid.length, hostile.length);
});

test("each malformed row says HOW it was malformed, not merely that it was", () => {
  // Which way a row is wrong says which thing broke. An array where a row was expected is a different
  // defect from a row whose pid did not survive serialisation, and an operator chasing one is not
  // helped by being told the other happened.
  const cases = [
    [[], /array/],
    [{}, /no process id/],
    [new Date(0), /no process id/],
    [{ id: "p", pid: null }, /no usable pid/],
    [{ id: "p", pid: 0 }, /no usable pid/],
    [{ id: "p", pid: -1 }, /no usable pid/],
    [{ id: "p", pid: NaN }, /no usable pid/],
    [{ id: "p", pid: "123" }, /no usable pid/],
    [{ id: "   ", pid: 5 }, /no process id/],
    [{ id: 7, pid: 5 }, /no process id/],
    [null, /not an object/],
    ["nope", /not an object/],
  ];
  for (const [row, expected] of cases) {
    const { invalid } = unownedProcessDecision([row], OWNERS, REMOVALS);
    assert.equal(invalid.length, 1, `${JSON.stringify(row)} was not rejected`);
    assert.match(invalid[0].why, expected, `${JSON.stringify(row)} gave the wrong reason`);
  }
});

test("a row with a pid but NO LABEL is still a valid row -- unclaimed is a state, not a defect", () => {
  // The three situations must stay distinguishable: a malformed row, an unattributable one, and one
  // whose start cannot be ordered. Requiring a label here would collapse the second into the first and
  // tell an operator the listing is broken when it is merely reporting something real.
  const { keep, invalid } = unownedProcessDecision([proc("p1", 111, "")], OWNERS, REMOVALS);
  assert.deepEqual(invalid, []);
  assert.match(keep[0].why, /no label/);
});

test("a row with no start time is a valid row too -- unorderable is a typed outcome", () => {
  const { keep, invalid } = unownedProcessDecision(
    [{ id: "p1", pid: 111, label: "apg-pilot-01" }],
    { "apg-pilot-01": OWNER_REMOVED }, { "apg-pilot-01": REMOVED_AT },
  );
  assert.deepEqual(invalid, []);
  assert.match(keep[0].why, /cannot be placed/);
});

test("no candidate can ever be reported without a usable pid to name it by", () => {
  // The failure was not just a misclassification: describeDecision printed "gone pid null". A report
  // an operator cannot act on is worse than no report, because it looks actionable.
  const rows = [...FLEET, { id: "x", pid: null, label: "apg-pilot-01", startedAtMs: BEFORE }];
  const { candidates } = unownedProcessDecision(rows, OWNERS, REMOVALS);
  for (const c of candidates) {
    assert.equal(typeof c.pid, "number");
    assert.ok(Number.isFinite(c.pid) && c.pid > 0, `candidate had pid ${c.pid}`);
  }
  const line = describeDecision(unownedProcessDecision(rows, OWNERS, REMOVALS));
  assert.doesNotMatch(line, /pid null|pid undefined|pid NaN/);
});

test("A MALFORMED ROW IS RETURNED, not skipped -- the partition covers every input", () => {
  // THIS TEST USED TO BLESS THE BUG. It asserted junk was "skipped rather than crashing" and checked
  // only that the two good lists were right, which documented a hole instead of closing it: the JSDoc
  // promised a complete partition while the loop dropped rows on the floor. A malformed row means the
  // listing is not the shape this code thinks it is, and that is a fact about the instrument -- so it
  // has to reach the caller, or a truncated listing reads as a clean fleet.
  const junk = [null, "nope", 7];
  const { candidates, keep, invalid } = unownedProcessDecision(
    [...junk, proc("p", 1, "apg-pilot-01")], OWNERS, REMOVALS,
  );
  assert.equal(candidates.length, 1);
  assert.equal(keep.length, 0);
  assert.equal(invalid.length, junk.length);
  assert.deepEqual(invalid.map((r) => r.raw), junk);
  // Cause-specific, so this asserts the cause these three actually have rather than a blanket phrase.
  for (const row of invalid) assert.match(row.why, /not an object/);
});

test("the count of every input is conserved even when the listing is mostly junk", () => {
  // CONSERVATION ALONE IS NOT ENOUGH and this test used to be the only one guarding the partition.
  // It passed against a build that classified arrays and Dates as processes, because the total was
  // still right. It stays because a dropped row is a real failure too, but the population tests above
  // are what actually hold the boundary.
  const rows = [null, undefined, 0, "", [], {}, new Date(0), { id: "z", pid: null },
    proc("p", 1, "apg-pilot-01"), proc("q", 2, "")];
  const { candidates, keep, invalid } = unownedProcessDecision(rows, OWNERS, REMOVALS);
  assert.equal(candidates.length + keep.length + invalid.length, rows.length);
});

test("THE THREE ARMS ARE DISTINCT: observed-empty, absent, and malformed", () => {
  // An EXPLICIT empty array is an observation: the caller looked and there was nothing.
  assert.deepEqual(unownedProcessDecision([], {}, {}),
    { candidates: [], keep: [], invalid: [], unreadable: null });

  // OMITTED and explicit `undefined` are not observations at all. A `processes = []` default made
  // them indistinguishable from the line above, so both answered "none to report" -- and in
  // JavaScript `undefined` is exactly what a missing field or a failed lookup returns.
  // `listing.processes` after a key rename is `undefined`, not evidence of an empty fleet.
  assert.match(unownedProcessDecision().unreadable, /no process listing was supplied/);
  assert.match(unownedProcessDecision(undefined, {}, {}).unreadable, /no process listing was supplied/);

  // MALFORMED says something different again, and keeps saying which thing arrived.
  assert.match(unownedProcessDecision({}, {}, {}).unreadable, /an object, not an array/);
});

test("the absent arm and the malformed arm do not share a message", () => {
  // Collapsing them would tell an operator chasing a renamed field that the producer sent the wrong
  // type, and vice versa. Two different bugs, two different first places to look.
  const absent = unownedProcessDecision().unreadable;
  const malformed = unownedProcessDecision("bad").unreadable;
  assert.notEqual(absent, malformed);
});

test("AN UNREADABLE LISTING IS REFUSED, not normalised into an empty fleet", () => {
  // THE TEST HERE USED TO BLESS THIS. It asserted `unownedProcessDecision(null, null, null)` equalled
  // three empty buckets and called that "junk arguments do not throw" -- so a listing that could not be
  // read rendered as "0 process(es) classified, none to report", which is an all-clear derived from
  // having examined nothing. Review probed it with null, {}, "bad" and 42.
  //
  // ROW CONSERVATION CANNOT SEE THIS. It counts rows against an input length, and when the container
  // is unreadable there are no rows on either side, so the arithmetic is perfect and means nothing.
  for (const bad of [null, {}, "bad", 42, true, () => {}]) {
    const out = unownedProcessDecision(bad, {}, {});
    assert.ok(out.unreadable, `${String(bad)} was accepted as a listing`);
    assert.deepEqual(out.candidates, []);
    assert.deepEqual(out.keep, []);
    assert.deepEqual(out.invalid, []);
  }
});

test("the refusal names what arrived, so the broken producer can be found", () => {
  assert.match(unownedProcessDecision(null).unreadable, /null/);
  assert.match(unownedProcessDecision("bad").unreadable, /a string/);
  assert.match(unownedProcessDecision(42).unreadable, /a number/);
  assert.match(unownedProcessDecision({}).unreadable, /an object/);
});

test("A REFUSED LISTING READS AS A REFUSAL, never as a clean fleet", () => {
  // The wording is the whole point. An operator who skims "none to report" stops looking.
  const line = describeDecision(unownedProcessDecision(null));
  assert.match(line, /could not be read/);
  assert.match(line, /NOTHING was classified/);
  assert.match(line, /not an empty fleet/);
  assert.doesNotMatch(line, /none to report/);
});

test("a readable listing carries unreadable:null, so the field is never merely absent", () => {
  // A caller testing `if (out.unreadable)` must get a defined answer on both paths; an absent key and
  // a null one read the same in JS but not to a reader deciding whether the field exists at all.
  const out = unownedProcessDecision(FLEET, OWNERS, REMOVALS);
  assert.equal(out.unreadable, null);
  assert.ok("unreadable" in out);
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
  // "CLASSIFIED", NOT "OWNED": `keep` also holds rows whose ownership was never established and rows
  // with no label to attribute at all, so calling them owned asserts the one thing that was not
  // determined about them.
  assert.match(line, /classified/);
  assert.doesNotMatch(line, /process\(es\) owned/);
});

test("a malformed listing is DISCLOSED in the report, on both paths", () => {
  // A row this code cannot read makes every count beside it a count of what was readable. Saying so
  // only when there is nothing to report would disclose it exactly when it matters least -- the same
  // mistake `context-window`'s fan-out cap made, and the same fix.
  const junk = [null, "nope"];
  const withCandidates = describeDecision(unownedProcessDecision([...junk, ...FLEET], OWNERS, REMOVALS));
  const withNone = describeDecision(unownedProcessDecision(
    [...junk, proc("p", 1, "graph-senior-dev")], { "graph-senior-dev": OWNER_ALIVE }, {},
  ));
  for (const line of [withCandidates, withNone]) {
    assert.match(line, /2 row\(s\) in the listing were not process rows/);
    assert.match(line, /counts are of what was readable/);
  }
});

test("a clean listing says nothing about malformed rows", () => {
  // The disclosure has to be absent when there is nothing to disclose, or it is noise rather than a
  // signal and stops being read.
  const line = describeDecision(unownedProcessDecision(FLEET, OWNERS, REMOVALS));
  assert.doesNotMatch(line, /not process rows/);
});

// -- processRowProblem, tested directly ----------------------------------------------------------
//
// THE PREDICATE THE WHOLE BOUNDARY RESTS ON, so it is exercised by name rather than only through the
// classifier. Testing it only through `unownedProcessDecision` proves the pair agree, which is a
// weaker claim than either being right -- and this predicate is where the shipped defect lived.

test("a well-formed row has no problem", () => {
  assert.equal(processRowProblem({ id: "p1", pid: 111, label: "a", startedAtMs: 1 }), null);
});

test("label and start time are OPTIONAL -- their absence is a state, not a malformed row", () => {
  // Requiring them would collapse "unclaimed" and "unorderable" into "broken listing", and those are
  // three different things an operator needs told apart.
  assert.equal(processRowProblem({ id: "p1", pid: 111 }), null);
  assert.equal(processRowProblem({ id: "p1", pid: 111, label: "" }), null);
  assert.equal(processRowProblem({ id: "p1", pid: 111, startedAtMs: "nonsense" }), null);
});

test("it rejects everything that is not an object", () => {
  for (const v of [null, undefined, 0, 1, "", "row", true, false, Symbol("s"), 9n]) {
    assert.match(processRowProblem(v), /not an object/, `${String(v)} was accepted`);
  }
});

test("AN ARRAY IS REJECTED -- typeof [] is \"object\", which is how one walked through the old guard", () => {
  assert.match(processRowProblem([]), /array/);
  assert.match(processRowProblem([1, 2, 3]), /array/);
});

test("an id must be a non-empty string, not merely present", () => {
  for (const id of [undefined, null, "", "   ", "\t\n", 7, {}, []]) {
    assert.match(processRowProblem({ id, pid: 111 }), /no process id/, `id ${String(id)} was accepted`);
  }
});

test("a pid must be a SAFE POSITIVE INTEGER -- finite and positive is not enough", () => {
  // `"123"` survives a truthiness check and 0 survives a typeof check, which is why both were already
  // here. Review then found `{id:"p", pid:0.5}` accepted: there is no process one-half. And `1e21` IS
  // an integer by `Number.isInteger`, but it sits past 2^53 where values stop being exactly
  // representable -- an identity that cannot be represented exactly cannot address a process exactly.
  const rejected = [
    undefined, null, 0, -1, -0.5, 0.5, 1.5, 1e21, Number.MAX_VALUE,
    NaN, Infinity, -Infinity, "123", "", {}, [], true,
  ];
  for (const pid of rejected) {
    assert.match(processRowProblem({ id: "p", pid }), /no usable pid/, `pid ${String(pid)} was accepted`);
  }
  for (const pid of [1, 111, 239888, Number.MAX_SAFE_INTEGER]) {
    assert.equal(processRowProblem({ id: "p", pid }), null, `pid ${String(pid)} was rejected`);
  }
});

test("id is checked before pid, so a row missing both names the id", () => {
  // Not a preference: a stable order means an operator seeing two runs of the same broken listing gets
  // the same reason both times, instead of one that depends on field iteration.
  assert.match(processRowProblem({}), /no process id/);
});

test("a Date is rejected, and for the reason a Date is wrong here", () => {
  // It is an object and not an array, so only the identity fields catch it. Review used exactly this
  // value, and under the old guard it was classified as an unlabelled process.
  assert.match(processRowProblem(new Date(0)), /no process id/);
});

test("describeDecision() WITH NOTHING refuses, rather than synthesising an all-clear", () => {
  // The same fabricated absence one layer up. Defaulting every field meant a call with no argument at
  // all produced "0 process(es) classified, none to report" -- reachable exactly the way the listing
  // default was, by a caller reading a key that no longer exists on the shape it was handed.
  for (const nothing of [undefined, null, "", 0, false, "decision", 42, []]) {
    const line = describeDecision(nothing);
    assert.match(line, /No decision was supplied/, `${String(nothing)} was described as a result`);
    assert.match(line, /not a clean result/);
    assert.doesNotMatch(line, /none to report/);
  }
});

test("a real decision is still described normally", () => {
  // The refusal above must not swallow the ordinary path.
  const line = describeDecision(unownedProcessDecision(FLEET, OWNERS, REMOVALS));
  assert.doesNotMatch(line, /No decision was supplied/);
  assert.match(line, /apg-pilot-01 pid 32456/);
});
