// A process aify-env is running is one the control plane can account for.
//
// THE OPERATOR ASKED FOR IT BY NAME, 2026-08-28: "i have 1 agent running in env but dashboard does not
// show him (that is why i wanted aify-env side running process visibility, to catch orphans like
// that)".
//
// MEASURED THE SAME EVENING. aify-env owned one PTY, pid 155844, whose command line read
// `claude-aify --aify-agent ef-manager --auto --resume ...`. The control plane's terminal row for that
// pid said `stopped`, and all 80 most recent sessions said `stopped`. The agent read `available` with
// a fresh `lastSeen`, because the orphan was heartbeating on its own behalf.
//
// NOTHING COULD HAVE CAUGHT IT, and that is the more interesting half: the API had no way to LIST
// terminals. It could fetch one by id and claim controls for them. So no screen and no check could ask
// "which terminals are live?", let alone compare that to a host. `GET /api/v1/terminals` was added in
// the same change, because a join needs both sides enumerable.
//
// THE KEY WAS ALREADY THERE. `terminal_sessions.process_id` holds the OS pid -- 99 of 103 rows numeric,
// measured -- and aify-env reports `pid` per process. Two lists nobody had put beside each other.
import assert from "node:assert/strict";
import { test } from "node:test";

import { LIVE_TERMINAL_STATUSES, envProcessVerdict, reconcileEnvProcesses } from "../env-process-reconciliation.mjs";

const ours = (pid, over = {}) => ({ id: `p${pid}`, pid, service: "aify-comms", label: "", ...over });
const live = (pid, over = {}) => ({
  id: `term_${pid}`, agentId: "an-agent", status: "attached", processId: pid,
  environmentId: "windows:host:default", ...over,
});

const ENV = "windows:host:default";

test("the live-status vocabulary matches the one the service filters on", () => {
  // TWO SIDES OF ONE QUESTION, in two languages. The service's `LIVE_TERMINAL_STATUSES` decides
  // which rows `GET /terminals` returns by default; this one decides which rows count as
  // accounting for a process. If they drift, a terminal can be live enough to be listed and not
  // live enough to match -- and every process behind one becomes a reported orphan.
  //
  // Stated here rather than imported: it is Python over there, and the agreement is what a test
  // can hold when a shared import cannot.
  assert.deepEqual(
    [...LIVE_TERMINAL_STATUSES].sort(),
    ["active", "attached", "idle", "running", "starting"],
    "the JS live-status set drifted from service/api_core/terminal_status.py",
  );
});

test("a process with a live terminal is accounted for", () => {
  const result = reconcileEnvProcesses({
    envProcesses: [ours(155844)], terminals: [live(155844)], environmentId: ENV,
  });
  assert.deepEqual(result.unaccounted, []);
  assert.deepEqual(result.phantom, []);
  assert.equal(result.matched, 1);
});

test("THE OPERATOR'S CASE: a running process whose terminal says stopped", () => {
  const result = reconcileEnvProcesses({
    envProcesses: [ours(155844)],
    terminals: [live(155844, { status: "stopped" })],
    environmentId: ENV,
  });
  assert.deepEqual(result.unaccounted, [{ id: "p155844", pid: "155844", label: "" }]);
  assert.equal(result.matched, 0);
});

test("a process with no terminal row at all is unaccounted for", () => {
  const result = reconcileEnvProcesses({ envProcesses: [ours(999)], terminals: [], environmentId: ENV });
  assert.equal(result.unaccounted.length, 1);
});

test("the label travels when aify-env has one, and its absence is not read as 'no agent'", () => {
  // The label is empty on every process a pre-fix bridge started. A reader treating that as "no agent"
  // would report the orphan as belonging to nobody, which is the wrong half of the truth.
  const named = reconcileEnvProcesses({
    envProcesses: [ours(1, { label: "ef-manager" })], terminals: [], environmentId: ENV,
  });
  assert.equal(named.unaccounted[0].label, "ef-manager");
  const blank = reconcileEnvProcesses({ envProcesses: [ours(1)], terminals: [], environmentId: ENV });
  assert.equal(blank.unaccounted[0].label, "");
  assert.equal(blank.unaccounted.length, 1, "a process with no label stopped being reported");
});

test("another service's processes are not judged", () => {
  // aify-env is a shared tier. Calling another service's process an orphan asserts knowledge of
  // records this bridge cannot read.
  const result = reconcileEnvProcesses({
    envProcesses: [ours(1, { service: "somebody-else" })], terminals: [], environmentId: ENV,
  });
  assert.deepEqual(result.unaccounted, []);
});

test("a live terminal whose pid nothing is running is a phantom", () => {
  // The other direction, and it costs differently: dispatches route to it and wait for a turn that
  // cannot start.
  const result = reconcileEnvProcesses({
    envProcesses: [], terminals: [live(4242, { agentId: "sc-coder" })], environmentId: ENV,
  });
  assert.deepEqual(result.phantom, [
    { terminalId: "term_4242", agentId: "sc-coder", pid: "4242", status: "attached" },
  ]);
});

test("ANOTHER HOST'S terminals are not phantoms", () => {
  // A doctor run probes the aify-env on ITS host. Comparing across environments would report every
  // other machine's live terminals as missing -- an alarm that fires on a healthy fleet is one nobody
  // reads on the day something is wrong.
  const result = reconcileEnvProcesses({
    envProcesses: [], terminals: [live(4242, { environmentId: "wsl:other:default" })], environmentId: ENV,
  });
  assert.deepEqual(result.phantom, []);
});

test("an UNKNOWN environment loses the phantom direction rather than reporting every host", () => {
  // The rule this module got wrong first: an empty id skipped the scoping guard, so every live
  // terminal everywhere was compared and reported. The comment claimed the opposite. A caller that
  // cannot say which environment is its own cannot tell a missing terminal from somebody else's.
  //
  // The unaccounted direction is UNAFFECTED and must stay so: a process aify-env is running either
  // matches a live pid or it does not, and that needs no environment at all.
  const result = reconcileEnvProcesses({
    envProcesses: [ours(1)],
    terminals: [live(777, { environmentId: "wsl:anywhere:default" })],
    environmentId: "",
  });
  assert.deepEqual(result.phantom, [], "phantoms were reported without knowing which environment is ours");
  assert.equal(result.unaccounted.length, 1, "the unaccounted direction was lost with the phantom one");
});

test("two unknowns do not make a match", () => {
  // THE EDGE THAT SEPARATES THE TWO GUARDS, found by a mutation that survived. Dropping the outer
  // `environmentId ? ... : []` leaves the inner comparison, and for every realistic row that is
  // enough -- `"wsl:x" !== ""` excludes it. It stops being enough when the TERMINAL's environment
  // is also empty: two blanks compare equal, and a row nobody can attribute gets reported against
  // an environment nobody could identify. Both sides unknown is the least safe moment to conclude.
  const result = reconcileEnvProcesses({
    envProcesses: [],
    terminals: [live(777, { environmentId: "" })],
    environmentId: "",
  });
  assert.deepEqual(result.phantom, [], "a terminal with no environment was matched to no environment");
});

test("a terminal that is not live is neither matched nor missing", () => {
  // `stopped` asserts nothing about a process existing, so its absence is not a fault.
  const result = reconcileEnvProcesses({
    envProcesses: [], terminals: [live(7, { status: "stopped" })], environmentId: ENV,
  });
  assert.deepEqual(result.phantom, []);
  assert.equal(result.matched, 0);
});

test("a non-numeric process_id is not joined against", () => {
  // 4 of 103 rows hold something that is not a pid. Coercing one would manufacture a join between two
  // unrelated strings, and the report would name the wrong agent.
  const result = reconcileEnvProcesses({
    envProcesses: [ours(155844)],
    terminals: [live(155844, { processId: "handle-abc" })],
    environmentId: ENV,
  });
  assert.equal(result.unaccounted.length, 1, "a bogus process_id was matched against a real pid");
  assert.deepEqual(result.phantom, [], "a row with no usable pid was reported as a phantom");
});

test("numeric pids compare across string and number", () => {
  // aify-env sends a number; SQLite hands back whatever was stored. A type mismatch here would report
  // every process as an orphan.
  const result = reconcileEnvProcesses({
    envProcesses: [{ id: "p1", pid: 155844, service: "aify-comms" }],
    terminals: [live("155844")],
    environmentId: ENV,
  });
  assert.equal(result.matched, 1);
  assert.deepEqual(result.unaccounted, []);
});

test("missing and malformed input produce no findings rather than throwing", () => {
  for (const input of [
    {}, { envProcesses: null, terminals: null }, { envProcesses: [null], terminals: [null] },
    { envProcesses: [{}], terminals: [{}] },
  ]) {
    assert.doesNotThrow(() => reconcileEnvProcesses(input));
  }
});

// ---- the verdict --------------------------------------------------------------------------------

test("a clean comparison passes and says what it compared", () => {
  const verdict = envProcessVerdict({
    result: { unaccounted: [], phantom: [], matched: 3 }, envAnswered: true,
  });
  assert.equal(verdict.ok, true);
  assert.match(verdict.detail, /3 process/);
});

test("NO EVIDENCE IS NOT A PASS: an unanswered aify-env is unknown, not ok", () => {
  // This repo's rule, written after `env-bridge` reported "2 connected" with zero bridges alive.
  const verdict = envProcessVerdict({ result: null, envAnswered: false });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "unknown");
});

test("a TRUNCATED listing is unknown, not a pile of orphans", () => {
  // The subtle one. Rows past the limit are absent from the comparison, so every process they belong
  // to would be reported as unaccounted -- a check inventing findings out of its own bound.
  const verdict = envProcessVerdict({
    result: { unaccounted: [{ id: "p1", pid: "1", label: "" }], phantom: [], matched: 0 },
    envAnswered: true,
    listingTruncated: true,
  });
  assert.equal(verdict.code, "unknown");
  assert.match(verdict.detail, /truncated/);
});

test("an orphan is named with whatever identity exists", () => {
  // "1 orphan" sends an operator looking. "p1 (pid 155844, ef-manager)" tells them which window.
  const verdict = envProcessVerdict({
    result: { unaccounted: [{ id: "p1", pid: "155844", label: "ef-manager" }], phantom: [], matched: 0 },
    envAnswered: true,
  });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "unaccounted");
  assert.match(verdict.detail, /p1 \(pid 155844, ef-manager\)/);
});

test("a phantom names the agent, because that is what an operator restarts", () => {
  const verdict = envProcessVerdict({
    result: { unaccounted: [], phantom: [{ terminalId: "t1", agentId: "sc-coder", pid: "9", status: "attached" }], matched: 0 },
    envAnswered: true,
  });
  assert.equal(verdict.code, "phantom");
  assert.match(verdict.detail, /sc-coder/);
  assert.match(verdict.fix, /Restart/i);
});

test("both kinds at once are both reported", () => {
  // Reporting only the first would hide half a broken host, and the two have different remedies.
  const verdict = envProcessVerdict({
    result: {
      unaccounted: [{ id: "p1", pid: "1", label: "" }],
      phantom: [{ terminalId: "t2", agentId: "b", pid: "2", status: "attached" }],
      matched: 0,
    },
    envAnswered: true,
  });
  assert.match(verdict.detail, /NO live terminal/);
  assert.match(verdict.detail, /not running/);
});
