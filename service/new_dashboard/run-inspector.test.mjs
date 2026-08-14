// The run inspector, tested by CALLING it.
//
// A "run" is one dispatched unit of work. This is where an operator sees what happened to one and
// where they press the four controls that act on a run already in flight — steer, interrupt, retry,
// close. Every one of those is a real effect on a live agent, and none of it was reachable by a test
// while it lived in app.js.
//
// The centre of gravity here is the CAPABILITY GATE. `handleRunInspectorControl` re-derives what is
// permitted before acting rather than trusting the button that was clicked, and the reason is that
// the button was rendered from data that may be ~15 seconds old: a run that has since completed still
// shows an enabled Interrupt. Acting on a stale button is how an operator interrupts the wrong thing.

import assert from "node:assert/strict";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { state } from "./state.mjs";
import {
  handleRunInspectorControl,
  initRunInspector,
  loadMoreRunEvents,
  openRunInspector,
  renderRunInspector,
  renderRuns,
  toggleRunEventOrder,
} from "./run-inspector.mjs";

function makeEl(extra = {}) {
  return {
    innerHTML: "", textContent: "", className: "", value: "", hidden: false, disabled: false,
    offsetParent: {}, dataset: {}, style: {}, children: [], firstElementChild: null,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    querySelector: () => null, querySelectorAll: () => [],
    addEventListener() {}, removeEventListener() {}, appendChild() {}, setAttribute() {},
    remove() {}, focus() {}, scrollTo() {},
    ...extra,
  };
}

/**
 * A dialog overlay that ANSWERS.
 *
 * `uiConfirm`/`uiPrompt` build a real modal and await a button click — there is no `window.confirm`
 * fallback to stub. So the fake element resolves the dialog the moment it is appended, by invoking
 * whichever button's click listener the answer calls for. Faking the answer any more cheaply (say,
 * by stubbing `uiConfirm`) is not possible from here: ESM bindings are read-only.
 */
function makeDialog(answer, typed) {
  const listeners = new Map();
  const button = (key) => makeEl({ addEventListener: (ev, fn) => { if (ev === "click") listeners.set(key, fn); } });
  const confirmBtn = button("confirm");
  const cancelBtn = button("cancel");
  const input = makeEl({ value: typed ?? "" });
  const overlay = makeEl({
    querySelector: (sel) => {
      if (sel === ".dialog-confirm") return confirmBtn;
      if (sel === ".dialog-cancel") return cancelBtn;
      if (sel === ".dialog-input") return input;
      return null;
    },
    querySelectorAll: () => [cancelBtn, confirmBtn],
  });
  overlay.__answer = () => {
    // The prompt's value is read off the input at click time, so set it first.
    input.value = typed ?? "";
    const fn = listeners.get(answer ? "confirm" : "cancel");
    if (fn) fn();
  };
  return overlay;
}

/**
 * Install DOM + fetch fakes and seed the module. `posts` records every mutating request, which is the
 * observable that matters: a control either reached the server or it did not.
 */
function withInspector(run, { confirm = true, prompt = "typed text", deps = {} } = {}) {
  const els = new Map();
  const posts = [];
  const saved = { document: globalThis.document, fetch: globalThis.fetch, requestAnimationFrame: globalThis.requestAnimationFrame };
  globalThis.requestAnimationFrame = (fn) => { fn(); return 1; };
  globalThis.document = {
    getElementById: (id) => { if (!els.has(id)) els.set(id, makeEl()); return els.get(id); },
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => makeDialog(confirm, prompt),
    addEventListener() {},
    removeEventListener() {},
    body: {
      // ANSWERED ON A MICROTASK, not inline. `openDialog` appends the overlay FIRST and registers the
      // button listeners after, so clicking during `appendChild` finds nothing wired and the promise
      // never settles — which shows up as "Promise resolution is still pending", not as a failed
      // assertion. Deferring past the synchronous remainder of `openDialog` is what makes it real.
      appendChild: (el) => { if (el && el.className === "dialog-overlay") queueMicrotask(() => el.__answer()); },
      classList: { add() {}, remove() {} },
      style: { setProperty() {} },
    },
    activeElement: null,
  };
  globalThis.fetch = async (url, options = {}) => {
    const method = (options.method || "GET").toUpperCase();
    if (method !== "GET") posts.push({ url: String(url), method, body: options.body });
    return { ok: true, status: 200, statusText: "OK", text: async () => JSON.stringify({ runs: [], events: [], run }) };
  };
  setApiBase("");
  const calls = { refresh: 0, openRunConsole: 0, closeInspector: 0, evaluateFlowGates: 0, openInspector: 0, renderDiagnosticsBulkToolbar: 0 };
  initRunInspector({
    closeInspector: () => { calls.closeInspector += 1; },
    evaluateFlowGates: () => { calls.evaluateFlowGates += 1; },
    openInspector: () => { calls.openInspector += 1; },
    openRunConsole: () => { calls.openRunConsole += 1; },
    refresh: async () => { calls.refresh += 1; },
    renderDiagnosticsBulkToolbar: () => { calls.renderDiagnosticsBulkToolbar += 1; },
    ...deps,
  });
  Object.assign(state, {
    runs: [], sessions: [], agents: [], filter: "",
    runFromFilter: "", runToFilter: "", runRuntimeFilter: "", runSearch: "", runStatusFilter: "",
    inspector: { run, events: [], source: "chat", sourceMessageId: "", eventOrder: "desc", eventPage: 0, hasMore: false },
  });
  return { els, posts, calls, restore: () => Object.assign(globalThis, saved) };
}

const RUNNING = { id: "run-1", status: "running", targetAgentId: "coder", from: "manager", runtime: "claude" };
const COMPLETED = { id: "run-2", status: "completed", targetAgentId: "coder", from: "manager", runtime: "claude" };

test("INIT REFUSES A PARTIAL BAG", () => {
  const full = {
    closeInspector() {}, evaluateFlowGates() {}, openInspector() {},
    openRunConsole() {}, refresh: async () => {}, renderDiagnosticsBulkToolbar() {},
  };
  for (const missing of Object.keys(full)) {
    const partial = { ...full };
    delete partial[missing];
    assert.throws(() => initRunInspector(partial), new RegExp(missing), `omitting ${missing} must throw`);
  }
  assert.doesNotThrow(() => initRunInspector(full));
});

test("A CONTROL ON A COMPLETED RUN DOES NOTHING — the stale-button case", async () => {
  // The drawer is rendered from data up to a poll old. A run that finished since then still shows an
  // enabled Interrupt, and clicking it must not reach the server. The gate is re-derived at ACTION
  // time for exactly this.
  // `await`, not `return promise.then(...)` inside try/finally: the finally would restore the DOM and
  // fetch fakes the instant the promise was RETURNED, while the control was still running against them.
  const h = withInspector(COMPLETED);
  try {
    await handleRunInspectorControl("interrupt");
    assert.deepEqual(h.posts, [], "no request may be sent for a run that is no longer active");
    assert.equal(h.calls.refresh, 0);
  } finally { h.restore(); }
});

test("interrupt on a RUNNING run posts the control and refreshes", async () => {
  const h = withInspector(RUNNING);
  try {
    await handleRunInspectorControl("interrupt");
    assert.equal(h.posts.length, 1, "an active run must actually be interrupted");
    assert.match(h.posts[0].url, /\/dispatch\/runs\/run-1\/control$/);
    assert.equal(h.posts[0].method, "POST");
    assert.deepEqual(JSON.parse(h.posts[0].body).action, "interrupt");
    assert.equal(h.calls.refresh, 1, "the page must reflect the new state, not the old one");
  } finally { h.restore(); }
});

test("DECLINING THE CONFIRMATION SENDS NOTHING", async () => {
  // Interrupt kills a live run and its pending controls. A confirmation that is asked and then ignored
  // is worse than none, because the operator believes they cancelled.
  const h = withInspector(RUNNING, { confirm: false });
  try {
    await handleRunInspectorControl("interrupt");
    assert.deepEqual(h.posts, [], "answering No must abort the control");
  } finally { h.restore(); }
});

test("steer with an EMPTY body sends nothing", async () => {
  // A steer with no text would deliver an empty turn to the agent.
  for (const typed of ["", "   ", null]) {
    const h = withInspector(RUNNING, { prompt: typed });
    try {
      await handleRunInspectorControl("steer");
      assert.deepEqual(h.posts, [], `steering with ${JSON.stringify(typed)} must send nothing`);
    } finally { h.restore(); }
  }
});

test("steer with text posts the typed body verbatim", async () => {
  const h = withInspector(RUNNING, { prompt: "focus on the failing test" });
  try {
    await handleRunInspectorControl("steer");
    assert.equal(h.posts.length, 1);
    const sent = JSON.parse(h.posts[0].body);
    assert.equal(sent.action, "steer");
    assert.equal(sent.body, "focus on the failing test", "the operator's text must reach the agent unaltered");
    assert.equal(sent.from_agent, "dashboard");
  } finally { h.restore(); }
});

test("open-console delegates instead of posting, and returns before the control chain", async () => {
  // It opens a UI panel; there is nothing to send and nothing to refresh.
  const h = withInspector(RUNNING, { deps: {} });
  try {
    state.sessions = [{ id: "s1", agentId: "coder", runId: "run-1" }];
    await handleRunInspectorControl("open-console");
    assert.deepEqual(h.posts, []);
  } finally { h.restore(); }
});

test("an UNKNOWN action and a run with no id are both no-ops", async () => {
  for (const [run, action] of [[RUNNING, "not-a-control"], [{ status: "running" }, "interrupt"], [RUNNING, ""]]) {
    const h = withInspector(run);
    try {
      await handleRunInspectorControl(action);
      assert.deepEqual(h.posts, [], `${JSON.stringify(action)} on ${JSON.stringify(run.id)} must do nothing`);
    } finally { h.restore(); }
  }
});

test("a failing control TELLS THE OPERATOR rather than failing silently", async () => {
  const h = withInspector(RUNNING);
  try {
    globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
    await assert.doesNotReject(() => handleRunInspectorControl("interrupt"),
      "a network failure must be caught, not left as an unhandled rejection");
  } finally { h.restore(); }
});

test("the drawer renders a loading state rather than throwing when no run is loaded", () => {
  const h = withInspector(null);
  try {
    renderRunInspector();
    assert.match(h.els.get("inspector-content").innerHTML, /Loading run inspector/);
  } finally { h.restore(); }
});

test("the drawer renders the run's identity and status", () => {
  const h = withInspector(RUNNING);
  try {
    renderRunInspector();
    const html = h.els.get("inspector-content").innerHTML;
    assert.match(html, /run-1/);
    assert.match(html, /coder/, "the target agent must be named");
    assert.match(html, /manager/, "…and who triggered it");
  } finally { h.restore(); }
});

test("the Runs list filters by TO and RUNTIME, which are the two helpers that came with it", () => {
  // `runTo` and `runRuntime` were one-line consts left orphaned in app.js when their callers moved.
  // They read three field spellings each, because the API has changed shape twice.
  const h = withInspector(null);
  try {
    state.runs = [
      { id: "r1", status: "running", target_agent: "coder", requested_runtime: "codex" },
      { id: "r2", status: "running", targetAgentId: "tester", runtime: "claude" },
    ];
    state.runToFilter = "coder";
    renderRuns();
    const shown = h.els.get("run-list").innerHTML;
    assert.match(shown, /r1/);
    assert.doesNotMatch(shown, /\br2\b/, "a run for another target must be filtered out");

    state.runToFilter = "";
    state.runRuntimeFilter = "claude";
    renderRuns();
    const byRuntime = h.els.get("run-list").innerHTML;
    assert.match(byRuntime, /r2/, "runtime must match the `runtime` spelling");
    assert.doesNotMatch(byRuntime, /\br1\b/, "…and exclude the codex run");
  } finally { h.restore(); }
});

test("FLIPPING THE ORDER CLEARS THE ACCUMULATED EVENTS", async () => {
  // Paging is cursor-based: each page is fetched `before` the last event already held. Flipping the
  // order without clearing would append the ascending first page underneath the descending one, giving
  // the operator a timeline running in two directions with no sign that it had changed.
  const h = withInspector(RUNNING);
  try {
    state.inspector.runId = "run-1";
    state.inspector.eventOrder = "desc";
    state.inspector.events = [{ id: "e1" }, { id: "e2" }];
    await toggleRunEventOrder();
    assert.equal(state.inspector.eventOrder, "asc", "the order must flip");
    assert.deepEqual(state.inspector.events, [], "…and the descending events must not survive it");
  } finally { h.restore(); }
});

test("load-more APPENDS from the last event as cursor, rather than replacing the page", async () => {
  const h = withInspector(RUNNING);
  let asked = null;
  try {
    state.inspector.runId = "run-1";
    state.inspector.events = [{ id: "e1" }, { id: "e2" }];
    globalThis.fetch = async (url) => {
      asked = String(url);
      return { ok: true, status: 200, statusText: "OK", text: async () => JSON.stringify({ events: [{ id: "e3" }], hasMore: false }) };
    };
    await loadMoreRunEvents();
    assert.deepEqual(state.inspector.events.map((e) => e.id), ["e1", "e2", "e3"], "the page must be appended");
    assert.match(asked, /before=e2/, "the cursor must be the LAST event held, not the first");
  } finally { h.restore(); }
});

test("a SECOND load-more while one is in flight does nothing", async () => {
  // A double-click would otherwise fetch the same cursor twice and append the page twice — visible as
  // duplicated events in the timeline, which reads as the agent having repeated itself.
  const h = withInspector(RUNNING);
  let fetches = 0;
  try {
    state.inspector.runId = "run-1";
    state.inspector.events = [{ id: "e1" }];
    let release;
    const gate = new Promise((r) => { release = r; });
    globalThis.fetch = async () => {
      fetches += 1;
      await gate;
      return { ok: true, status: 200, statusText: "OK", text: async () => JSON.stringify({ events: [{ id: "e2" }] }) };
    };
    const first = loadMoreRunEvents();
    const second = loadMoreRunEvents();
    release();
    await Promise.all([first, second]);
    assert.equal(fetches, 1, "the re-entrancy guard must hold");
    assert.deepEqual(state.inspector.events.map((e) => e.id), ["e1", "e2"], "…and the page lands exactly once");
  } finally { h.restore(); }
});

test("openRunInspector records where the operator came from", async () => {
  // The drawer's Back path and the follow-up's threading both read this. Losing it strands a reply.
  const h = withInspector(null);
  try {
    await openRunInspector({ runId: "run-1", source: "chat", sourceMessageId: "msg-9" });
    assert.equal(state.inspector.source, "chat");
    assert.equal(state.inspector.sourceMessageId, "msg-9");
  } finally { h.restore(); }
});

test("the six injected names are NOT imported", async () => {
  const fs = await import("node:fs");
  const src = fs.readFileSync(new URL("./run-inspector.mjs", import.meta.url), "utf8");
  for (const name of ["closeInspector", "evaluateFlowGates", "openInspector",
    "openRunConsole", "refresh", "renderDiagnosticsBulkToolbar"]) {
    assert.doesNotMatch(src, new RegExp(`^import .*\\b${name}\\b`, "m"), `${name} must be injected`);
  }
});
