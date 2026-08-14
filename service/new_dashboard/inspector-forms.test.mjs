// Real tests for the inspector's form and detail panels.
//
// `buildHandoffPacket` is the one with logic worth pinning: it is the text an operator pastes into another
// agent to hand work over, so a filter that misses one leg of a conversation hands over a half-transcript
// and the receiving agent answers the wrong question. It had no test while it lived in app.js.
//
// SEALING. `state` is a shared singleton, so every field read here is rebuilt per test; `document` does not
// exist in Node and is installed only while rendering.

import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { state } from "./state.mjs";
import {
  buildHandoffPacket,
  openAgentEditForm,
  openCompactionHistory,
  openMessageDetail,
} from "./inspector-forms.mjs";

function el() {
  const classes = new Set();
  return {
    innerHTML: "",
    value: "",
    classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c) },
  };
}

function render(run) {
  const els = { "inspector-content": el(), inspector: el() };
  const had = { d: "document" in globalThis, r: "requestAnimationFrame" in globalThis };
  // createElement/body are here because `openMessageDetail` reports a miss through `toast()`, which
  // builds a real node of its own. Without them the failure reads 'document.createElement is not a
  // function', which points at the harness rather than at anything being tested.
  const kids = [];
  globalThis.document = {
    getElementById: (id) => els[id] || null,
    querySelector: () => null,
    createElement: () => ({
      className: "", textContent: "", children: [], firstElementChild: null,
      setAttribute() {}, remove() {}, addEventListener() {},
      classList: { add() {}, remove() {} },
      appendChild: (c) => c,
    }),
    body: { appendChild: (c) => { kids.push(c); return c; } },
  };
  globalThis.requestAnimationFrame = (fn) => fn();
  try {
    run(els);
    return els["inspector-content"].innerHTML;
  } finally {
    if (!had.d) delete globalThis.document;
    if (!had.r) delete globalThis.requestAnimationFrame;
  }
}

test("the handoff packet collects BOTH legs of the conversation", () => {
  // Filtering on `from` alone would hand over only what the agent said and none of what it was asked —
  // the receiving agent then answers a question it cannot see.
  state.messages = [
    { from: "coder", to: "manager", body: "done" },
    { from: "manager", to: "coder", body: "please do it" },
    { from: "tester", to: "manager", body: "unrelated" },
  ];
  const packet = buildHandoffPacket("coder");
  assert.ok(packet.includes("done"));
  assert.ok(packet.includes("please do it"), "inbound messages must be included, not just outbound");
  assert.ok(!packet.includes("unrelated"), "another pair's traffic must not leak into the handoff");
});

test("the `target` recipient spelling counts as a leg", () => {
  // Dispatch-authored rows carry `target` rather than `to`; missing them silently truncates the handoff.
  state.messages = [{ from: "manager", target: "coder", body: "via target" }];
  assert.ok(buildHandoffPacket("coder").includes("via target"));
});

test("the packet keeps the LAST N messages and says how many it has", () => {
  // `.slice(-count)`: a handoff is about recent context, and taking the first N would hand over the
  // beginning of a long conversation instead of where it got to.
  state.messages = Array.from({ length: 40 }, (_, i) => ({ from: "coder", to: "manager", body: `msg ${i}` }));
  const packet = buildHandoffPacket("coder", 5);
  assert.ok(packet.includes("msg 39"), "the newest message must be present");
  assert.ok(!packet.includes("msg 34"), "…and anything older than the window must not");
  assert.ok(packet.includes("last 5 messages"), "the header states how much context is actually included");
});

test("a message with no body falls back to its preview, and an empty conversation still yields a packet", () => {
  state.messages = [{ from: "coder", to: "manager", preview: "just a preview" }];
  assert.ok(buildHandoffPacket("coder").includes("just a preview"));

  state.messages = [];
  const empty = buildHandoffPacket("ghost");
  assert.ok(empty.includes("ghost"), "an empty packet must still name the agent it is about");
  assert.ok(empty.includes("last 0 messages"));
});

test("opening a message that is not loaded warns instead of rendering an empty panel", () => {
  // The detail panel is reachable from a row the poll may have dropped. A blank drawer reads as a bug.
  state.messages = [];
  const html = render(() => openMessageDetail("no-such-id"));
  assert.equal(html, "", "nothing must be rendered for a message that is not there");
});

test("a loaded message renders its from/to and falls back on the target spelling", () => {
  state.messages = [{ id: "m1", from: "manager", target: "coder", type: "task", body: "the body" }];
  const html = render(() => openMessageDetail("m1"));
  assert.ok(html.includes("manager"));
  assert.ok(html.includes("coder"), "the `target` spelling must render as the recipient");
  assert.ok(html.includes("the body"));
});

test("the agent edit form opens for an agent that is no longer in state", () => {
  // Reachable from a drawer the poll has since emptied; throwing here leaves the panel half-written.
  state.agents = [];
  const html = render(() => openAgentEditForm("ghost"));
  assert.ok(html.includes("ghost"));
});

test("the edit form pre-fills from the agent's current record", () => {
  // An edit form that opens blank invites the operator to overwrite fields they meant to leave alone.
  // The form carries description, session handle, environment and runtime — NOT role, which my first
  // version of this test asserted from memory instead of from the markup.
  state.agents = [{ id: "coder", description: "does the work", sessionHandle: "sess-9", runtime: "codex" }];
  state.environments = [];
  const html = render(() => openAgentEditForm("coder"));
  assert.ok(html.includes("does the work"), "the current description must be pre-filled");
  assert.ok(html.includes("sess-9"), "…and the native session handle");
  assert.ok(html.includes('value="codex" selected'), "…and the runtime must come up selected");
});

test("hermes is offered under its canonical backend identifier", () => {
  // Asserted in app.test.mjs as a source regex until v0.5.4. The identifier matters: the dashboard sends
  // this value straight through, so an option labelled anything else would register a runtime the
  // backend does not know.
  state.agents = [{ id: "coder" }];
  state.environments = [];
  const html = render(() => openAgentEditForm("coder"));
  assert.ok(html.includes('value="hermes"'), "hermes must be selectable");
});

test("an unknown runtime is added to the options rather than silently reset", () => {
  // The runtime list is a fixed set plus whatever the agent actually has. Without that union an agent on
  // a runtime the dashboard does not know would open the form showing 'generic' — and saving would
  // change its runtime as a side effect of opening a form.
  state.agents = [{ id: "coder", runtime: "some-future-runtime" }];
  state.environments = [];
  const html = render(() => openAgentEditForm("coder"));
  assert.ok(html.includes('value="some-future-runtime" selected'));
});

// --- the history panel ----------------------------------------------------
//
// Joined this module in v0.5.4. It is the only inspector panel that FETCHES, which is what makes it worth
// its own harness: it renders a placeholder, then either a list or an error, and the error path is the one
// an operator meets when the service is down.
//
// A REAL LOOPBACK SERVER, because `api` is an imported binding. And an ASYNC render helper: the sync
// `render()` above returns the moment the promise is created, so every await would run with the globals
// already torn down — a mistake I made once already in this series and which reads as broken code rather
// than a broken harness.

const HISTORY_SERVER = http.createServer((req, res) => {
  req.on("data", () => {});
  req.on("end", () => HISTORY_HANDLER(req, res));
});
let HISTORY_HANDLER = (_req, res) => { res.writeHead(200); res.end("{}"); };
const HISTORY_PORT = await new Promise((r) => HISTORY_SERVER.listen(0, "127.0.0.2", () => r(HISTORY_SERVER.address().port)));
setApiBase(`http://127.0.0.2:${HISTORY_PORT}/api/v1`);
test.after(() => HISTORY_SERVER.close());

function serveHistory(payload, status = 200) {
  HISTORY_HANDLER = (_req, res) => {
    res.writeHead(status, { "content-type": "application/json" });
    res.end(JSON.stringify(payload));
  };
}

async function renderAsync(run) {
  const els = { "inspector-content": el(), inspector: el() };
  const had = { d: "document" in globalThis, r: "requestAnimationFrame" in globalThis };
  globalThis.document = {
    getElementById: (id) => els[id] || null,
    querySelector: () => null,
    createElement: () => ({
      className: "", textContent: "", children: [], firstElementChild: null,
      setAttribute() {}, remove() {}, addEventListener() {},
      classList: { add() {}, remove() {} },
      appendChild: (c) => c,
    }),
    body: { appendChild: (c) => c },
  };
  globalThis.requestAnimationFrame = (fn) => fn();
  try {
    await run(els);
    return { html: els["inspector-content"].innerHTML, els };
  } finally {
    if (!had.d) delete globalThis.document;
    if (!had.r) delete globalThis.requestAnimationFrame;
  }
}

test("the history panel claims the inspector and records what it is showing", async () => {
  // `state.inspector.kind` is what the rest of the app reads to know which panel is open; leaving a stale
  // kind there makes a later refresh redraw the previous panel over this one.
  serveHistory({ spawnRequests: [] });
  state.inspector = { kind: "agent", runId: "run-9", agentId: "other" };
  const { els } = await renderAsync(() => openCompactionHistory("coder-1"));

  assert.equal(state.inspector.kind, "history");
  assert.equal(state.inspector.agentId, "coder-1");
  assert.equal(state.inspector.runId, "", "a stale runId must be cleared, not carried over");
  assert.equal(els.inspector.classList.contains("open"), true);
  assert.equal(els.inspector.classList.contains("run-inspector-sheet"), false,
    "the run-sheet layout belongs to a different panel and must be removed");
});

test("a spawn record for ANOTHER agent is not shown in this agent's history", async () => {
  // Three id shapes are accepted because the records come from different writers. Matching too widely
  // would attribute another agent's spawn to this one.
  serveHistory({
    spawnRequests: [
      { agentId: "coder-1", createdAt: "2026-08-01T00:00:00Z", metadata: {} },
      { agent_id: "coder-1", createdAt: "2026-08-02T00:00:00Z", metadata: {} },
      { agentId: "someone-else", createdAt: "2026-08-03T00:00:00Z", metadata: {} },
      { agentId: "x", createdAt: "2026-08-04T00:00:00Z", metadata: { continuedFromAgentId: "coder-1" } },
    ],
  });
  state.inspector = {};
  const { html } = await renderAsync(() => openCompactionHistory("coder-1"));
  assert.ok(!html.includes("someone-else"), "another agent's record must not appear");
});

test("an unreachable service explains itself instead of rendering an empty history", async () => {
  // THE PATH AN OPERATOR ACTUALLY MEETS. An empty panel here is indistinguishable from "this agent has
  // never been compacted", which is the wrong conclusion to hand someone debugging a restart.
  serveHistory({ detail: "database is locked" }, 500);
  state.inspector = {};
  const { html } = await renderAsync(() => openCompactionHistory("coder-1"));
  assert.match(html, /Could not load spawn records/);
  assert.match(html, /database is locked/, "the reason must reach the operator, not just the failure");
});

test("an agent id containing markup is escaped into the panel", async () => {
  serveHistory({ spawnRequests: [] });
  state.inspector = {};
  const { html } = await renderAsync(() => openCompactionHistory('<img src=x onerror="alert(1)">'));
  assert.ok(!html.includes("<img src=x"), "the raw tag must not survive");
  assert.ok(html.includes("&lt;img"), "it must appear escaped");
});

test("the payload may be {spawnRequests}, {requests} or a bare array", async () => {
  // `res.spawnRequests || res.requests || res || []` — all three have been returned by this endpoint.
  for (const payload of [
    { spawnRequests: [{ agentId: "coder-1", createdAt: "2026-08-01", metadata: {} }] },
    { requests: [{ agentId: "coder-1", createdAt: "2026-08-01", metadata: {} }] },
    [{ agentId: "coder-1", createdAt: "2026-08-01", metadata: {} }],
  ]) {
    serveHistory(payload);
    state.inspector = {};
    const { html } = await renderAsync(() => openCompactionHistory("coder-1"));
    assert.ok(!html.includes("Could not load"), `shape ${JSON.stringify(payload).slice(0, 24)} must be accepted`);
  }
});
