// Console actions, tested by CALLING them.
//
// The failure this pane can have is a console that LOOKS live and is not, and `resyncActiveConsole` is
// the recovery for it. Three things in it are load-bearing and none was covered:
//
//   - the RE-ENTRANCY guard, because a PTY resize emits a burst of repaint frames which can themselves
//     look like a sequence gap, which starts another resync and another resize — the observed
//     153↔154-cols flicker loop;
//   - the SEQUENCE floor, because the snapshot must never move `lastSeq` BACKWARDS, or frames already
//     painted are painted again;
//   - `reset()` before the repaint, because these TUIs redraw only what changed, so a screen with
//     wrong rows keeps them forever — this is the "refresh does not actually fix it" complaint.

import assert from "node:assert/strict";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { state } from "./state.mjs";
import {
  initConsoleActions,
  openRunConsole,
  resyncActiveConsole,
  startConsoleForSession,
  stopConsoleTerminal,
} from "./console-actions.mjs";

function makeEl(extra = {}) {
  return {
    innerHTML: "", textContent: "", className: "", value: "", hidden: false, disabled: false,
    dataset: {}, style: {}, children: [], firstElementChild: null, offsetParent: {},
    clientWidth: 800, clientHeight: 600,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    querySelector: () => null, querySelectorAll: () => [], appendChild() {}, setAttribute() {},
    addEventListener() {}, removeEventListener() {}, remove() {}, focus() {},
    getBoundingClientRect: () => ({ width: 800, height: 600 }),
    ...extra,
  };
}

function makeDialog(answer) {
  const listeners = new Map();
  const button = (key) => makeEl({ addEventListener: (ev, fn) => { if (ev === "click") listeners.set(key, fn); } });
  const confirmBtn = button("confirm");
  const cancelBtn = button("cancel");
  const overlay = makeEl({
    querySelector: (sel) => ({ ".dialog-confirm": confirmBtn, ".dialog-cancel": cancelBtn, ".dialog-input": null }[sel] ?? null),
    querySelectorAll: () => [cancelBtn, confirmBtn],
  });
  overlay.__answer = () => { const fn = listeners.get(answer ? "confirm" : "cancel"); if (fn) fn(); };
  return overlay;
}

/** A fake xterm entry that records what was done to it. */
function makeEntry(over = {}) {
  const wrote = [];
  let resets = 0;
  return {
    terminalId: "t1",
    lastSeq: 10,
    fitCols: 100,
    ownsPty: false,
    container: makeEl(),
    term: { cols: 120, rows: 40, write: (s) => wrote.push(s), reset: () => { resets += 1; }, resize() {} },
    get __wrote() { return wrote; },
    get __resets() { return resets; },
    ...over,
  };
}

function withConsole({ confirm = true, snapshot = { terminal: { snapshot: "CLEAN", outputSeq: 12 } } } = {}) {
  const els = new Map();
  const sent = [];
  const gets = [];
  const saved = { document: globalThis.document, fetch: globalThis.fetch, requestAnimationFrame: globalThis.requestAnimationFrame };
  globalThis.requestAnimationFrame = (fn) => { fn(); return 1; };
  globalThis.document = {
    getElementById: (id) => { if (!els.has(id)) els.set(id, makeEl()); return els.get(id); },
    querySelector: () => null, querySelectorAll: () => [],
    createElement: () => makeDialog(confirm),
    addEventListener() {}, removeEventListener() {},
    body: {
      appendChild: (el) => { if (el && el.className === "dialog-overlay") queueMicrotask(() => el.__answer()); },
      classList: { add() {}, remove() {} }, style: { setProperty() {} },
    },
    activeElement: null,
  };
  globalThis.fetch = async (url, options = {}) => {
    const method = (options.method || "GET").toUpperCase();
    if (method === "GET") gets.push(String(url)); else sent.push({ url: String(url), method, body: options.body });
    return { ok: true, status: 200, statusText: "OK", json: async () => snapshot, text: async () => JSON.stringify(snapshot) };
  };
  setApiBase("");
  const calls = { refresh: 0, refreshSoon: 0, closeInspector: 0, setPage: [] };
  initConsoleActions({
    closeInspector: () => { calls.closeInspector += 1; },
    refresh: async () => { calls.refresh += 1; },
    refreshSoon: () => { calls.refreshSoon += 1; },
    setPage: (p) => { calls.setPage.push(p); },
  });
  Object.assign(state, { sessions: [], agents: [], activeXterm: null, chat: { ...(state.chat || {}) } });
  return { els, sent, gets, calls, restore: () => Object.assign(globalThis, saved) };
}

test("resync with NO mounted console is a no-op", async () => {
  // It runs from the WS reconnect path, which fires whether or not a console is open.
  const h = withConsole();
  try {
    state.activeXterm = null;
    await assert.doesNotReject(() => resyncActiveConsole());
    state.activeXterm = { terminalId: "t1" };  // mounted but no term yet
    await assert.doesNotReject(() => resyncActiveConsole());
    assert.deepEqual(h.gets, [], "nothing may be fetched without a live terminal");
  } finally { h.restore(); }
});

test("resync RESETS before repainting — the 'refresh does not actually fix it' complaint", async () => {
  // These TUIs repaint only what changed, so writing a clean snapshot over a scrambled screen leaves
  // the scramble. `reset()` (not `clear()`) is what wipes the alt-screen/scrollback state first.
  const h = withConsole();
  try {
    const entry = makeEntry();
    state.activeXterm = entry;
    await resyncActiveConsole();
    assert.equal(entry.__resets, 1, "the terminal must be reset before the snapshot is written");
    assert.deepEqual(entry.__wrote, ["CLEAN"]);
  } finally { h.restore(); }
});

test("resync fetches at the FITTED width, not the current one", async () => {
  // The current width may already be the widened one. Fetching at it stops the server re-inferring
  // the source width, and the console comes back at the wrong size — which is what widened it.
  const h = withConsole();
  try {
    state.activeXterm = makeEntry({ fitCols: 100, term: { cols: 153, rows: 40, write() {}, reset() {} } });
    await resyncActiveConsole();
    assert.equal(h.gets.length, 1);
    assert.match(h.gets[0], /cols=100/, "the fitted width must be what is asked for");
    assert.doesNotMatch(h.gets[0], /cols=153/);
  } finally { h.restore(); }
});

test("THE RE-ENTRANCY GUARD HOLDS — one gap cannot fan out into a resize loop", async () => {
  // A PTY resize emits a burst of repaint frames, which can themselves expose a transient seq gap,
  // which starts another resync and another resize. This is the observed 153↔154-cols flicker.
  const h = withConsole();
  try {
    let release;
    const gate = new Promise((r) => { release = r; });
    let fetches = 0;
    globalThis.fetch = async () => {
      fetches += 1;
      await gate;
      return { ok: true, status: 200, statusText: "OK", text: async () => JSON.stringify({ terminal: { snapshot: "X" } }) };
    };
    state.activeXterm = makeEntry();
    const first = resyncActiveConsole();
    const second = resyncActiveConsole();
    release();
    await Promise.all([first, second]);
    assert.equal(fetches, 1, "a resync already in flight must swallow the second");
  } finally { h.restore(); }
});

test("the guard CLEARS afterwards, so a later gap can still recover", async () => {
  // A guard that leaked would make the console permanently unrecoverable — strictly worse than the
  // flicker it was added to stop.
  const h = withConsole();
  try {
    const entry = makeEntry();
    state.activeXterm = entry;
    await resyncActiveConsole();
    await resyncActiveConsole();
    assert.equal(h.gets.length, 2, "a second resync after the first completed must run");
    assert.equal(entry.resyncing, false);
  } finally { h.restore(); }
});

test("the guard clears even when the FETCH FAILS", async () => {
  const h = withConsole();
  try {
    const entry = makeEntry();
    state.activeXterm = entry;
    globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
    await assert.doesNotReject(() => resyncActiveConsole());
    assert.equal(entry.resyncing, false, "a failed resync must not wedge the console forever");
  } finally { h.restore(); }
});

test("A FAILED RESYNC KEEPS THE CURRENT BUFFER rather than blanking the pane", async () => {
  const h = withConsole();
  try {
    const entry = makeEntry();
    state.activeXterm = entry;
    globalThis.fetch = async () => { throw new TypeError("down"); };
    await resyncActiveConsole();
    assert.deepEqual(entry.__wrote, [], "nothing may be written when nothing was fetched");
    assert.equal(entry.__resets, 0, "…and the screen must not be wiped either");
  } finally { h.restore(); }
});

test("THE SEQUENCE FLOOR NEVER MOVES BACKWARDS", async () => {
  // The snapshot's seq can lag the live stream. Taking it unconditionally would re-admit frames the
  // pane has already painted — the duplicate output an operator reads as the agent repeating itself.
  const h = withConsole({ snapshot: { terminal: { snapshot: "X", outputSeq: 3 } } });
  try {
    const entry = makeEntry({ lastSeq: 10 });
    state.activeXterm = entry;
    await resyncActiveConsole();
    assert.equal(entry.lastSeq, 10, "an older snapshot seq must not lower the floor");
  } finally { h.restore(); }

  const ahead = withConsole({ snapshot: { terminal: { snapshot: "X", outputSeq: 25 } } });
  try {
    const entry = makeEntry({ lastSeq: 10 });
    state.activeXterm = entry;
    await resyncActiveConsole();
    assert.equal(entry.lastSeq, 25, "…and a newer one must raise it");
  } finally { ahead.restore(); }
});

test("a NON-NUMERIC snapshot seq leaves the floor where it was", async () => {
  const h = withConsole({ snapshot: { terminal: { snapshot: "X", outputSeq: "not-a-number" } } });
  try {
    const entry = makeEntry({ lastSeq: 10 });
    state.activeXterm = entry;
    await resyncActiveConsole();
    assert.equal(entry.lastSeq, 10);
  } finally { h.restore(); }
});

test("forceRepaint only nudges the PTY when this pane OWNS it", async () => {
  // Resizing a PTY the pane does not own would reshape another viewer's terminal.
  const h = withConsole();
  try {
    state.activeXterm = makeEntry({ ownsPty: false });
    await resyncActiveConsole({ forceRepaint: true });
    assert.deepEqual(h.sent, [], "no resize may be sent for a PTY this pane does not own");
  } finally { h.restore(); }
});

test("STOPPING A CONSOLE TERMINAL IS CONFIRMED", async () => {
  const no = withConsole({ confirm: false });
  try {
    await stopConsoleTerminal("t1");
    assert.deepEqual(no.sent, [], "declining must not kill the terminal");
  } finally { no.restore(); }

  const yes = withConsole({ confirm: true });
  try {
    await stopConsoleTerminal("t1");
    assert.equal(yes.sent.length, 1);
    assert.match(yes.sent[0].url, /t1/);
  } finally { yes.restore(); }
});

test("starting a console for a session posts and refreshes", async () => {
  const h = withConsole({ confirm: true });
  try {
    await startConsoleForSession("s1", false);
    assert.equal(h.sent.length, 1);
    assert.match(h.sent[0].url, /s1/);
  } finally { h.restore(); }
});

test("openRunConsole with a run that has NO session does not navigate", async () => {
  // Landing on the Console page with nothing to show reads as the console having died.
  const h = withConsole();
  try {
    state.sessions = [];
    openRunConsole({ id: "r1" });
    assert.deepEqual(h.calls.setPage, [], "no session, no navigation");
  } finally { h.restore(); }
});

test("INIT REFUSES A PARTIAL BAG", () => {
  const full = { closeInspector() {}, refresh: async () => {}, refreshSoon() {}, setPage() {} };
  for (const missing of Object.keys(full)) {
    const partial = { ...full };
    delete partial[missing];
    assert.throws(() => initConsoleActions(partial), new RegExp(missing), `omitting ${missing} must throw`);
  }
  assert.doesNotThrow(() => initConsoleActions(full));
});
