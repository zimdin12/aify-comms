// Steering a live run from the dashboard, and loading the run list — neither had ever been called.
//
// From the dashboard V8-coverage census: `requestRunControl` and `loadRunsForStatus` are exported from
// `run-inspector.mjs`, wired into app.js, and no test invokes either. `run-inspector.test.mjs` is
// thorough about rendering and about `handleRunInspectorControl`'s capability gating, but the two
// functions here reach the network on their own terms.
//
// WHY THESE TWO FIRST. `requestRunControl` INJECTS TEXT INTO A LIVE AGENT'S RUN. Its only gate is a
// prompt dialog, and the whole gate is `if (!body || !body.trim()) return;` — one line standing between
// a mis-click and an empty steer landing in an agent's turn. A cancelled dialog resolves `null` and a
// confirmed-but-empty one resolves `""`, which are different values from different user actions and are
// both supposed to end here. Nothing had ever checked that.
//
// `loadRunsForStatus` writes `state.runs`, which `renderRuns` immediately iterates. Its `runs.runs || []`
// is the guard that keeps a response shape change from turning the Runs page into a crash instead of an
// empty list.
//
// THE PROMPT IS DRIVEN, NOT STUBBED. `uiPrompt` is imported by the product, so a test cannot replace it;
// it builds a dialog in the DOM and resolves when a button handler fires. The DOM below is the same
// shape `ui-dialog-keys.test.mjs` uses: `openDialog` finds `.dialog-confirm` / `.dialog-cancel` /
// `.dialog-input` by selector, so those are the only nodes that have to be real. Driving the real
// dialog also means these tests cover the actual value contract — `null` from cancel, the input's
// string from confirm — rather than a guess about it.

import assert from "node:assert/strict";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { state } from "./state.mjs";
import { loadRunsForStatus, requestRunControl } from "./run-inspector.mjs";

// ── a DOM just big enough for openDialog and toast ──────────────────────────

const dialogs = [];
const toasts = [];

function makeNode() {
  const controls = new Map();
  const node = {
    className: "", innerHTML: "", textContent: "", style: {}, dataset: {}, children: [],
    isConnected: true,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    setAttribute() {}, appendChild(child) { node.children.push(child); return child; },
    remove() { node.removed = true; },
    get firstElementChild() { return node.children[0] || null; },
    addEventListener() {}, removeEventListener() {},
    querySelectorAll: () => [],
    querySelector(selector) {
      if (!controls.has(selector)) {
        controls.set(selector, {
          value: "",
          focus() {},
          addEventListener(type, fn) { node.handlers.set(`${selector}:${type}`, fn); },
        });
      }
      return controls.get(selector);
    },
    handlers: new Map(),
  };
  return node;
}

globalThis.document = {
  body: { appendChild() {}, children: [], firstElementChild: null },
  activeElement: null,
  createElement: () => {
    const node = makeNode();
    // A toast is created with a className; a dialog overlay is created and then has markup assigned.
    // Both land here, so both are recorded and the tests read whichever they care about.
    dialogs.push(node);
    toasts.push(node);
    return node;
  },
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener() {},
  removeEventListener() {},
};
globalThis.requestAnimationFrame = (cb) => { cb(0); return 1; };

/** Answer the dialog `run()` opens: `null` cancels, a string confirms with that value. */
async function answerPrompt(run, answer) {
  dialogs.length = 0;
  const pending = run();
  await new Promise((r) => setTimeout(r, 0));   // let openDialog build and register its handlers
  const overlay = dialogs.find((node) => node.handlers.has(".dialog-confirm:click"));
  assert.ok(overlay, "no dialog was opened — the steer went out without asking anyone");
  if (answer === null) {
    overlay.handlers.get(".dialog-cancel:click")();
  } else {
    overlay.querySelector(".dialog-input").value = answer;
    overlay.handlers.get(".dialog-confirm:click")();
  }
  return pending;
}

function toastsSaid(fragment) {
  return toasts.filter((node) => String(node.textContent).includes(fragment)).length;
}

// ── fetch ───────────────────────────────────────────────────────────────────

const requests = [];
let responder = () => ({});

globalThis.fetch = async (url, options = {}) => {
  requests.push({ url: String(url), method: options.method || "GET", body: options.body });
  const answer = (await responder(String(url), options)) || {};
  return {
    ok: answer.ok !== false,
    status: answer.status || (answer.ok === false ? 500 : 200),
    statusText: answer.statusText || "OK",
    text: async () => (answer.body === undefined ? "{}" : answer.body),
  };
};

const BASE = "http://dashboard.invalid/api/v1";   // resolves nowhere: an escaped request fails loudly
setApiBase(BASE);

function posts() {
  return requests.filter((r) => r.method === "POST");
}

test.beforeEach(() => {
  requests.length = 0;
  toasts.length = 0;
  dialogs.length = 0;
  responder = () => ({});
  state.runs = [];
  state.runStatusFilter = "";
  state.inspector = null;
});

// ── requestRunControl: the one line between a mis-click and a live turn ─────

test("a CANCELLED prompt sends nothing", async () => {
  await answerPrompt(() => requestRunControl("run-1"), null);
  assert.deepEqual(posts(), [], "cancelling the steer dialog still posted to the run");
  assert.deepEqual(requests, [], "cancelling the steer dialog still talked to the service");
});

test("a CONFIRMED but empty prompt sends nothing either", async () => {
  // Different user action, different resolved value: cancel gives `null`, confirming an untouched input
  // gives `""`. Both must stop here, and only one of them is falsy for the reason you would guess.
  await answerPrompt(() => requestRunControl("run-1"), "");
  assert.deepEqual(posts(), [], "an empty steer was posted into a live run");
});

test("a WHITESPACE-ONLY steer sends nothing", async () => {
  // `!body.trim()` and not `!body`: a spacebar press is a non-empty string that says nothing. Delivered,
  // it would consume the agent's turn to read blanks.
  await answerPrompt(() => requestRunControl("run-1"), "   \n\t ");
  assert.deepEqual(posts(), [], "a whitespace-only steer reached the agent");
});

test("a real steer posts exactly once, with the run id encoded into the path", async () => {
  await answerPrompt(() => requestRunControl("run/7 8"), "please stop and summarise");
  const sent = posts();
  assert.equal(sent.length, 1, "the steer did not post exactly once");
  assert.equal(sent[0].url, `${BASE}/dispatch/runs/run%2F7%208/control`,
    "the run id was not encoded — a id with a slash would address a different endpoint");
  assert.deepEqual(JSON.parse(sent[0].body),
    { from_agent: "dashboard", action: "steer", body: "please stop and summarise" });
});

test("the steer body is sent VERBATIM — not trimmed, not escaped", async () => {
  // The trim is a GATE, not a transform: what the operator typed is what the agent must read, including
  // leading indentation and quotes. Pinned because "it was only used to check emptiness" is exactly the
  // kind of assumption a later refactor breaks.
  const body = '  keep   this "as-is"\nsecond line  ';
  await answerPrompt(() => requestRunControl("run-1"), body);
  assert.equal(JSON.parse(posts()[0].body).body, body);
});

test("after a successful steer the inspector is retargeted at that run", async () => {
  // The re-open is what shows the operator their steer landed. Asserted on the state `openRunInspector`
  // sets synchronously, not on the render — the render needs the whole inspector DOM, and asserting it
  // here would make this test fail for a reason that has nothing to do with steering.
  await answerPrompt(() => requestRunControl("run-42"), "status?");
  assert.equal(state.inspector?.kind, "run");
  assert.equal(state.inspector?.runId, "run-42");
  assert.equal(state.inspector?.source, "runs");
});

test("a REJECTED steer tells the operator instead of throwing", async () => {
  // This runs from a click handler; an escaping rejection is an unhandled rejection and the operator is
  // left believing a steer landed that never did.
  responder = (url) => (url.includes("/control")
    ? { ok: false, status: 409, body: JSON.stringify({ error: "run already finished" }) }
    : {});
  await assert.doesNotReject(() => answerPrompt(() => requestRunControl("run-9"), "hello"));
  assert.equal(toastsSaid("Steer failed"), 1, "a rejected steer was swallowed silently");
  assert.equal(toastsSaid("run already finished"), 1, "the operator was not told WHY it failed");
});

// ── loadRunsForStatus ───────────────────────────────────────────────────────

test("loading a status filter stores it, fetches for it, and returns the runs", async () => {
  responder = () => ({ body: JSON.stringify({ runs: [{ id: "r1" }, { id: "r2" }] }) });
  const runs = await loadRunsForStatus("queued", false);
  assert.equal(state.runStatusFilter, "queued");
  assert.equal(requests.length, 1);
  assert.match(requests[0].url, /queued/, "the status never reached the query");
  assert.deepEqual(runs, [{ id: "r1" }, { id: "r2" }]);
  assert.deepEqual(state.runs, runs, "state.runs and the return value disagree");
});

test("a response with NO runs key leaves an empty array, not undefined", async () => {
  // `runs.runs || []`. `renderRuns` iterates `state.runs` immediately; undefined there is a crashed
  // Runs page rather than an empty one, on a response shape the dashboard does not control.
  responder = () => ({ body: JSON.stringify({ ok: true }) });
  const runs = await loadRunsForStatus("", false);
  assert.deepEqual(runs, []);
  assert.deepEqual(state.runs, []);
});

test("OMITTING the status refreshes the current filter; passing an empty one clears it", async () => {
  // Two falsy-looking calls with opposite meanings, and the difference is easy to get backwards — I did,
  // and this test failed until I read the signature. `status = state.runStatusFilter` is a DEFAULT
  // PARAMETER, so `loadRunsForStatus()` is "reload what I am looking at" and only an EXPLICIT falsy value
  // reaches `status || ''` as a request for everything. Getting it backwards would make every background
  // refresh silently reset the operator's filter to All.
  responder = () => ({ body: JSON.stringify({ runs: [] }) });

  state.runStatusFilter = "failed";
  await loadRunsForStatus(undefined, false);
  assert.equal(state.runStatusFilter, "failed", "an argument-less refresh reset the operator's filter");
  assert.match(requests[requests.length - 1].url, /failed/, "the refresh did not query the current filter");

  await loadRunsForStatus("", false);
  assert.equal(state.runStatusFilter, "", "an explicit empty status did not clear the filter");
  assert.doesNotMatch(requests[requests.length - 1].url, /failed/, "the cleared filter still queried 'failed'");
});

test("a failing runs fetch REJECTS — it does not quietly leave a stale list", async () => {
  // Deliberately different from the steer path: this one has callers that await it, and swallowing here
  // would show the previous status's runs under the new filter's heading.
  responder = () => ({ ok: false, status: 500, body: JSON.stringify({ error: "db locked" }) });
  state.runs = [{ id: "stale" }];
  await assert.rejects(() => loadRunsForStatus("queued", false), /db locked/);
  assert.deepEqual(state.runs, [{ id: "stale" }], "a failed load half-cleared the list");
});

// ── the truncation flag travels with the rows ───────────────────────────────
//
// THE STATUS DROPDOWN IS THE ONE ACTION ON THIS PAGE THAT RE-QUERIES THE SERVER, and it went through
// here rather than through `runRefreshCycle` -- which stored `truncated` while this function did not.
// So picking a status whose whole result fits on a page still rendered "Older runs are not loaded" and
// the truncated empty state, carrying the PREVIOUS query's answer. The note claims to appear only when
// rows were left behind, and a stale flag makes that claim false at the one moment an operator is
// acting on it. Found in review; the producer/call-site class again, a value the response carries and
// one of two consumers drops.

test("A TRUE FLAG IS CLEARED BY A RESPONSE THAT SAYS FALSE", async () => {
  state.runsTruncated = true;
  responder = () => ({ body: JSON.stringify({ runs: [], truncated: false, limit: 80 }) });
  await loadRunsForStatus("queued", false);
  assert.equal(state.runsTruncated, false, "the page still claims older runs are not loaded, on a "
    + "result the server said was complete");
});

test("…and a false one is set by a response that says true", async () => {
  // The other direction, because a flag that only ever clears is a flag that never warns.
  state.runsTruncated = false;
  responder = () => ({ body: JSON.stringify({ runs: [{ id: "r1" }], truncated: true, limit: 80 }) });
  await loadRunsForStatus("", false);
  assert.equal(state.runsTruncated, true);
});

test("a response that says nothing about truncation is not truncated", async () => {
  // `Boolean(undefined)`. An older service, or an error shape, must not leave the note latched on --
  // and must not invent a warning either.
  state.runsTruncated = true;
  responder = () => ({ body: JSON.stringify({ ok: true }) });
  await loadRunsForStatus("", false);
  assert.equal(state.runsTruncated, false);
});

test("A FAILED FETCH CHANGES NEITHER THE ROWS NOR THE FLAG", async () => {
  // The rows and the flag are one fact and must move together. `api` throws on a non-ok response, so
  // both keep what the last successful load established -- the alternative is a page showing the old
  // rows under a freshly-computed note about a query that never returned.
  state.runs = [{ id: "kept" }];
  state.runsTruncated = true;
  responder = () => ({ ok: false, status: 503, body: JSON.stringify({ error: "down" }) });
  await assert.rejects(() => loadRunsForStatus("queued", false));
  assert.deepEqual(state.runs, [{ id: "kept" }]);
  assert.equal(state.runsTruncated, true);
});
