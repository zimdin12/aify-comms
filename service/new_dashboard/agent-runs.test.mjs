// B5's second half: what work reached this agent, in its drawer.
//
// The Processes panel beside it answers "what is running for it". This answers "what was it asked to
// do, and did any of it close" -- and the two are genuinely different: an agent can have three
// terminals and no runs, or a run that stranded a requester and no terminal at all.
//
// THE WINDOW IS PARTIAL, and most of these tests are about saying so. `state.runs` is a limited page
// (measured on the live database: a limit=80 page reached back only to 26 August), so an agent whose
// last run fell off it renders as an agent with no runs. "No work ever reached this agent" and "none
// in the page we loaded" are opposite answers, and a panel that gives the first when it means the
// second is worse than one that says nothing.

import assert from "node:assert/strict";
import { test } from "node:test";

import { AGENT_RUNS_ID, renderAgentRuns, runsForAgent } from "./agent-runs.mjs";

const run = (over = {}) => ({
  id: "run_1", targetAgentId: "sc-lead", status: "completed", subject: "review the diff",
  requestedAt: new Date().toISOString(), replyPending: false, ...over,
});

test("A RUN TARGETING THIS AGENT REACHES THE PANEL", () => {
  // POSITIVE CONTROL. Several assertions below are about absences, and an empty render satisfies
  // them while showing the operator nothing.
  const html = renderAgentRuns([run()], "sc-lead");
  assert.match(html, /review the diff/);
  assert.match(html, /completed/);
  assert.match(html, /Showing 1\./);
});

test("ANOTHER AGENT'S RUNS ARE NOT SHOWN", () => {
  // The filter is the panel. Without it this is the runs page in a smaller box.
  const html = renderAgentRuns([run({ targetAgentId: "sc-tester", subject: "not mine" })], "sc-lead");
  assert.ok(!/not mine/.test(html), "another agent's run appeared in this agent's drawer");
  assert.match(html, /No dispatch runs have targeted this agent/);
});

test("the agent is matched through the shared accessor, so field alternates work", () => {
  // `runTargetAgent` handles targetAgentId / target_agent / agentId / agent_id. A panel that read
  // one spelling would silently show nothing for rows carrying another -- and a gate already caught
  // a dead field alternate in this same drawer once.
  assert.equal(runsForAgent([{ target_agent: "sc-lead" }], "sc-lead").length, 1);
  assert.equal(runsForAgent([{ agentId: "sc-lead" }], "sc-lead").length, 1);
});

test("NEWEST FIRST, regardless of the order the page holds", () => {
  // `state.runs` is ordered for the runs PAGE. Showing five rows out of a page ordered for something
  // else shows an arbitrary five.
  const older = run({ id: "old", subject: "older", requestedAt: "2026-09-01T00:00:00Z" });
  const newer = run({ id: "new", subject: "newer", requestedAt: "2026-09-03T00:00:00Z" });
  const ordered = runsForAgent([older, newer], "sc-lead").map((r) => r.id);
  assert.deepEqual(ordered, ["new", "old"]);
});

test("THE TWO EMPTY STATES ARE DIFFERENT ANSWERS", () => {
  // The distinction this panel would otherwise get wrong, and the one `run-inspector.mjs` already
  // makes for the runs list.
  const never = renderAgentRuns([], "sc-lead", { truncated: false });
  const notLoaded = renderAgentRuns([], "sc-lead", { truncated: true });
  assert.match(never, /No dispatch runs have targeted this agent/);
  assert.match(notLoaded, /Older runs are not loaded/);
  assert.notEqual(never, notLoaded, "a partial window rendered the same as an empty history");
});

test("a truncated page SAYS SO even when rows were found", () => {
  // The dangerous case is not the empty one: three rows with no caveat reads as this agent's whole
  // history, which is exactly what it is not.
  const html = renderAgentRuns([run()], "sc-lead", { truncated: true });
  assert.match(html, /Older runs are not loaded/);
});

test("IT SAYS WHAT IT SHOWS OUT OF WHAT", () => {
  // "3 runs" beside a page holding seven of this agent's is a count of the panel, not of the agent,
  // and nothing on screen would say which.
  const many = Array.from({ length: 7 }, (_, i) => run({ id: `r${i}`, requestedAt: `2026-09-0${i + 1}T00:00:00Z` }));
  const html = renderAgentRuns(many, "sc-lead", { limit: 3 });
  assert.match(html, /Showing 3 of 7 loaded\./);
});

test("A REPLY OWED ON A SETTLED RUN IS CALLED OUT", () => {
  // The actionable state, invisible in a status column reading `completed`: a finished run that owes
  // a reply nobody sent is somebody waiting.
  const html = renderAgentRuns([run({ status: "completed", replyPending: true })], "sc-lead");
  assert.match(html, /reply owed/);
});

test("but NOT on a run that is still open, where pending means 'not yet'", () => {
  // Flagging every in-flight run would make the marker meaningless, which is the same as not having
  // it -- except it also cries wolf.
  for (const status of ["queued", "claimed", "running", "delivered"]) {
    const html = renderAgentRuns([run({ status, replyPending: true })], "sc-lead");
    assert.ok(!/reply owed/.test(html), `an open run (${status}) was flagged as owing a reply`);
  }
});

test("a subject is ESCAPED, not injected", () => {
  // A subject is operator- or agent-authored text that arrived over our own API. That does not make
  // it a trusted string.
  const html = renderAgentRuns([run({ subject: '<img src=x onerror="alert(1)">' })], "sc-lead");
  assert.ok(!/<img/.test(html), "a run subject was rendered as markup");
  assert.match(html, /&lt;img/);
});

test("a run with no subject falls back to its id rather than rendering blank", () => {
  const html = renderAgentRuns([run({ subject: "", id: "run_abc" })], "sc-lead");
  assert.match(html, /run_abc/);
});

test("an unparseable requestedAt renders an em dash, not an empty cell", () => {
  // `relTimeHtml` returns '' for a value it cannot parse as well as for a missing one.
  const html = renderAgentRuns([run({ requestedAt: "whenever" })], "sc-lead");
  assert.match(html, /—/);
  assert.ok(!/<td><\/td>/.test(html), "an unparseable timestamp left the cell empty");
});

test("A BLANK AGENT ID MATCHES NOTHING, including a run whose OWN target is blank", () => {
  // FAILS CLOSED, and the fixture matters more than the assertion here. My first version of this
  // test used a run with a real target -- which the filter excludes anyway, so deleting the guard
  // left it green and proved nothing. Measured: without `if (!id) return []`, a blank id paired
  // with a blank-target run returns 1 row, because `"" === ""` matches. That row is the only input
  // that reaches the guard, so it is the only one that tests it.
  assert.deepEqual(runsForAgent([{ targetAgentId: "" }], ""), [],
    "a run with no target matched a blank agent id");
  assert.deepEqual(runsForAgent([{}], ""), [], "a run with no target field at all matched");
  assert.deepEqual(runsForAgent([run()], null), []);
});

test("the container id is shared, not spelled twice", () => {
  assert.equal(typeof AGENT_RUNS_ID, "string");
  assert.ok(AGENT_RUNS_ID.length > 0);
});
