// B5: what is actually running for an agent, behind a click.
//
// The drawer already answered everything ABOUT an agent -- runtime, mode, environment, workspace,
// session, machine, last seen -- and nothing about its processes. The operator asked directly: "i
// cannot still check the processes themself? (like browse agent or something)". A pid was reachable
// only by reading the database by hand.
//
// THE RENDER IS PURE, so every case below is tested without a DOM, a fetch or a service. The loader
// is the only part that needs any of those and it is deliberately thin -- but it is NOT untested,
// because the interesting behaviour is what it renders when the read FAILS.

import assert from "node:assert/strict";
import { test } from "node:test";

import { AGENT_PROCESSES_ID, loadAgentProcesses, renderAgentProcesses } from "./agent-processes.mjs";

const terminal = (over = {}) => ({
  id: "term_1", status: "running", processId: "4242", cols: 157, rows: 32,
  runtime: "claude-code", updatedAt: new Date().toISOString(), ...over,
});

test("A TERMINAL'S ID, STATUS AND PID ALL REACH THE PANEL", () => {
  // POSITIVE CONTROL. Every assertion below is about a specific absence or substitution, and an
  // empty render would satisfy several of them while showing the operator nothing.
  const html = renderAgentProcesses([terminal()]);
  assert.match(html, /term_1/);
  assert.match(html, /running/);
  assert.match(html, /4242/, "the pid never reached the panel, and the pid is the join key");
  assert.match(html, /157x32/);
  assert.match(html, /1 terminal\(s\), 1 live\./);
});

test("A STOPPED ROW STILL HOLDING A PID IS SHOWN, because that is the orphan", () => {
  // The case this panel exists for. aify-env owned a live PTY for `ef-manager` (pid 155844) while
  // every recent session read `stopped` and the dashboard showed nothing. Filtering to live rows
  // would hide exactly that, which is why the loader asks for `status=all`.
  const html = renderAgentProcesses([terminal({ status: "stopped", processId: "155844" })]);
  assert.match(html, /155844/, "a stopped terminal's pid was dropped");
  assert.match(html, /stopped/);
  assert.match(html, /1 terminal\(s\), 0 live\./, "a stopped row was counted as live");
});

test("THE LOADER ASKS FOR status=all, and for this agent only", () => {
  // Asserted on the URL rather than on the result: a loader that fetched every terminal on the host
  // and filtered client-side would render identically here and be wrong at fleet scale.
  const asked = [];
  const host = { innerHTML: "" };
  return loadAgentProcesses("sc-lead", {
    api: async (path) => { asked.push(path); return { terminals: [] }; },
    byId: () => host,
  }).then(() => {
    assert.equal(asked.length, 1);
    assert.match(asked[0], /^\/terminals\?agentId=sc-lead&status=all$/, `asked: ${asked[0]}`);
  });
});

test("a missing pid renders an em dash, NEVER a zero", () => {
  // A pid of 0 and a pid nobody recorded are different facts, and the second must not read as the
  // first: an operator hunting an orphan would try to kill it.
  const html = renderAgentProcesses([terminal({ processId: "" })]);
  assert.ok(!/>0</.test(html), "a missing pid rendered as 0");
  assert.match(html, /—/);
});

test("a size is BOTH dimensions or neither", () => {
  // "157x0" invites somebody to believe the height is real.
  assert.ok(!/157x0/.test(renderAgentProcesses([terminal({ rows: 0 })])));
  assert.ok(!/0x32/.test(renderAgentProcesses([terminal({ cols: 0 })])));
});

test("A ZERO WIDTH IS FLAGGED, because it means the console renders at a GUESSED width", () => {
  // The B3 link, and the only place a person can see which terminals are exposed to it: cols is 0
  // until a resize control completes, and until then the snapshot is rendered at an inferred width
  // and re-wraps every line.
  const html = renderAgentProcesses([terminal({ cols: 0, rows: 0 })]);
  assert.match(html, /inferred width/, "a terminal with no recorded size says nothing about why");
});

test("an empty list SAYS WHY rather than rendering nothing", () => {
  // An absent section is indistinguishable from a broken feature -- the same argument the drawer's
  // CLI block already makes.
  assert.match(renderAgentProcesses([]), /No terminals have been created/);
  assert.match(renderAgentProcesses(undefined), /No terminals have been created/);
});

test("A FAILED READ IS NOT AN EMPTY LIST", () => {
  // Rendering "no processes" after a failed read tells the operator something false about their
  // fleet, which is worse than telling them the panel is broken.
  const html = renderAgentProcesses([], { error: "503 Service Unavailable" });
  assert.match(html, /Could not read/);
  assert.match(html, /503 Service Unavailable/);
  assert.ok(!/No terminals have been created/.test(html), "a failure claimed there are no terminals");
});

test("the loader turns a REJECTED read into that message, and never throws", () => {
  // Everything else in the drawer is still true whether this read succeeds or not, so a rejection
  // must not take the panel's neighbours down with it.
  const host = { innerHTML: "" };
  return loadAgentProcesses("sc-lead", {
    api: async () => { throw new Error("network down"); },
    byId: () => host,
  }).then(() => {
    assert.match(host.innerHTML, /Could not read/);
    assert.match(host.innerHTML, /network down/);
  });
});

test("a hostile terminal id is ESCAPED, not injected", () => {
  // These rows come from the service, and the service's rows come from hosts. An id is not a
  // trusted string just because it arrived over our own API.
  const html = renderAgentProcesses([terminal({ id: '<img src=x onerror="alert(1)">' })]);
  assert.ok(!/<img/.test(html), "a terminal id was rendered as markup");
  assert.match(html, /&lt;img/);
});

test("a row with no id is dropped rather than rendered blank", () => {
  // A malformed listing entry carrying only a status is not a terminal anybody can act on, and a
  // blank row invites a click that addresses nothing.
  assert.match(renderAgentProcesses([{ status: "running" }]), /No terminals have been created/);
});

test("an unparseable timestamp renders an em dash, not an empty cell", () => {
  // `relTimeHtml` returns '' for a value it cannot parse as well as for a missing one, so testing
  // the INPUT leaves an empty cell whenever the service sends something unexpected -- and an empty
  // cell reads as "no data" rather than "the value made no sense".
  const html = renderAgentProcesses([terminal({ updatedAt: "not-a-date" })]);
  assert.match(html, /—/);
  assert.ok(!/<td><\/td>/.test(html), "an unparseable timestamp left the cell empty");
});

test("the loader does nothing when its container is absent", () => {
  // The drawer may have been closed, or another agent selected, before the read returned. Writing
  // into a missing node is a crash; fetching for a panel nobody is looking at is waste.
  const asked = [];
  return loadAgentProcesses("sc-lead", {
    api: async (p) => { asked.push(p); return { terminals: [] }; },
    byId: () => null,
  }).then(() => assert.deepEqual(asked, [], "it fetched for a panel that is not on screen"));
});

test("the container id is shared, not spelled twice", () => {
  // The drawer writes the div and this module fills it; two spellings would agree until one was
  // edited, and the symptom would be a panel that silently never fills.
  assert.equal(typeof AGENT_PROCESSES_ID, "string");
  assert.ok(AGENT_PROCESSES_ID.length > 0);
});

// ── WHY a terminal ended, which the panel showed nothing of ───────────────────────────────────────
//
// MEASURED BEFORE BUILDING: of 40 live rows, 36 had ended and 33 of those carried a reason -- an
// `error` string, an `exitCode`, or a signal. The panel said only "stopped", which is the status a
// terminal reaches whether it was reaped, refused, superseded, or simply finished.

test("A STOPPED TERMINAL SAYS WHY, from its error text", () => {
  // The half a person can act on. These are real strings from the live service.
  const html = renderAgentProcesses([terminal({
    status: "stopped", error: 'this host is already running a worker for "sc-coder"',
  })]);
  assert.match(html, /already running a worker/);
});

test("the error text WINS over the numbers when both are present", () => {
  // `exit 1` beside a sentence explaining what happened adds nothing, and two reasons in one cell
  // reads as though the panel could not decide.
  const html = renderAgentProcesses([terminal({ status: "failed", exitCode: 1, error: "refused by the host" })]);
  assert.match(html, /refused by the host/);
  assert.ok(!/exit 1/.test(html), "both a reason and an exit code were rendered");
});

test("a signal is named, and an exit code is shown when that is all there is", () => {
  assert.match(renderAgentProcesses([terminal({ status: "stopped", error: "", exitSignal: "SIGTERM" })]), /killed by SIGTERM/);
  assert.match(renderAgentProcesses([terminal({ status: "stopped", error: "", exitCode: 137 })]), /exit 137/);
});

test("EXIT 0 IS A REASON, even though it is falsy", () => {
  // The bug a truthiness test would introduce: a terminal that exited CLEANLY is a different fact
  // from one that recorded nothing, and `if (code)` collapses them.
  const html = renderAgentProcesses([terminal({ status: "stopped", error: "", exitCode: 0 })]);
  assert.match(html, /exit 0/);
  assert.ok(!/no reason recorded/.test(html), "a clean exit was reported as unrecorded");
});

test("an ended row with NO reason says so rather than rendering blank", () => {
  // Three of the 36 live ended rows had none, and a blank there is indistinguishable from a cell
  // this panel forgot to fill.
  const html = renderAgentProcesses([terminal({ status: "stopped", error: "", exitCode: null, exitSignal: "" })]);
  assert.match(html, /no reason recorded/);
});

test("A LIVE TERMINAL GETS NO REASON ROW AT ALL", () => {
  // A running terminal has not ended, so there is nothing to explain. Rendering "no reason recorded"
  // beside `attached` would invent a problem.
  const html = renderAgentProcesses([terminal({ status: "attached", error: "stale text from an earlier life" })]);
  assert.ok(!/no reason recorded/.test(html));
  // ASSERTED ON THE TEXT, not on a marker class. My first version checked for the absence of
  // `agent-process-reason`, and when the colspan row was replaced by a line inside the status cell
  // that class stopped existing anywhere -- so the assertion passed by describing nothing. The
  // fixture now carries a stale `error` string, which is the input that would leak a reason onto a
  // live row, and the assertion is that it does not appear.
  assert.ok(!/stale text from an earlier life/.test(html),
    "a live terminal rendered an error left over from before it was restarted");
});

test("a reason is ESCAPED, because it carries host-authored text", () => {
  // These strings come from whatever refused or reaped the terminal. That is the least trusted text
  // in the whole panel.
  const html = renderAgentProcesses([terminal({ status: "failed", error: '<img src=x onerror="alert(1)">' })]);
  assert.ok(!/<img/.test(html), "an error string was rendered as markup");
  assert.match(html, /&lt;img/);
});
