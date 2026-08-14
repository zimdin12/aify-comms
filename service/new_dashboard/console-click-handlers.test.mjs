// The console toolbar's five-way action dispatch, tested by CALLING it.
//
// It lived inside app.js's delegated click handler and nothing could reach it. The shape is the reason
// this file exists: `if (action === 'copy') … else if … else if …` with NO final else. An action string
// that matches nothing falls through every branch and does nothing at all — no error, no log — so a
// renamed `data-console-action` in a template turns a toolbar button into a no-op that reads as fine.
//
// The three that are injected are also the three that MATTER most: stop kills a terminal, and the two
// starts differ only in a boolean that decides whether an existing session is resumed or discarded.
// Passing that boolean wrongly is not a cosmetic bug.

import assert from "node:assert/strict";
import test from "node:test";

import { runConsoleAction } from "./console-click-handlers.mjs";

/** Recorders for the three injected callbacks. */
function spies({ resyncRejects = false } = {}) {
  const calls = [];
  return {
    calls,
    names: () => calls.map((c) => c[0]),
    resync: (...a) => {
      calls.push(["resync", ...a]);
      return resyncRejects ? Promise.reject(new Error("gone")) : Promise.resolve();
    },
    stop: (...a) => { calls.push(["stop", ...a]); },
    start: (...a) => { calls.push(["start", ...a]); },
  };
}

/** `toast` reaches the DOM; stub enough of it for the refresh branch to run for real. */
function withDom(run) {
  const had = "document" in globalThis;
  const prev = globalThis.document;
  const hadRaf = "requestAnimationFrame" in globalThis;
  globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };
  const el = () => ({
    className: "", textContent: "", style: {}, dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    setAttribute() {}, appendChild() {}, remove() {}, addEventListener() {},
    querySelectorAll: () => [], firstChild: null, children: [],
  });
  globalThis.document = {
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: el,
    body: { appendChild() {}, contains: () => true },
  };
  return Promise.resolve(run()).finally(() => {
    if (had) globalThis.document = prev; else delete globalThis.document;
    if (!hadRaf) delete globalThis.requestAnimationFrame;
  });
}

const el = (action, extra = {}) => ({ dataset: { consoleAction: action, ...extra } });

test("AN UNRECOGNISED ACTION DOES NOTHING — and that is the silent failure to know about", () => {
  // There is no final `else`. This pins the current behaviour rather than endorsing it: if the template
  // ever renames an action, the button goes quiet and this test is the only place that says so.
  const s = spies();
  return withDom(() => {
    assert.doesNotThrow(() => runConsoleAction(el("no-such-action"), s.resync, s.stop, s.start));
    assert.doesNotThrow(() => runConsoleAction(el(undefined), s.resync, s.stop, s.start));
    assert.deepEqual(s.names(), [], "no callback fires for an unknown action");
  });
});

test("'stop' passes the TERMINAL id, not the session id", () => {
  // Two different ids live on the same element. Stopping by session id would address the wrong thing —
  // and this is the destructive action of the five.
  const s = spies();
  return withDom(() => {
    runConsoleAction(el("stop", { terminalId: "t-9", sessionId: "s-1" }), s.resync, s.stop, s.start);
    assert.deepEqual(s.calls, [["stop", "t-9"]]);
  });
});

test("'start' RESUMES and 'start-fresh' DISCARDS — the boolean is the whole difference", () => {
  // The two actions call the same function and differ only in the second argument. Swapped, "start"
  // would throw away a live session's history and "start fresh" would silently resume the thing the
  // operator asked to be rid of.
  const s = spies();
  return withDom(() => {
    runConsoleAction(el("start", { sessionId: "s-1" }), s.resync, s.stop, s.start);
    runConsoleAction(el("start-fresh", { sessionId: "s-1" }), s.resync, s.stop, s.start);
    assert.deepEqual(s.calls, [["start", "s-1", false], ["start", "s-1", true]]);
  });
});

test("'refresh' forces a repaint", () => {
  // `{ forceRepaint: true }`. Without it the resync can no-op against an unchanged buffer, which is
  // precisely the case the operator pressed the button for.
  const s = spies();
  return withDom(async () => {
    runConsoleAction(el("refresh"), s.resync, s.stop, s.start);
    await new Promise((r) => setTimeout(r, 0));
    assert.deepEqual(s.calls, [["resync", { forceRepaint: true }]]);
  });
});

test("a FAILED refresh is swallowed rather than escaping the click handler", () => {
  // `.catch(() => {})`. An unhandled rejection inside a delegated listener surfaces as an unrelated
  // console error, and the console is exactly where an operator is already looking for a problem.
  const s = spies({ resyncRejects: true });
  return withDom(async () => {
    assert.doesNotThrow(() => runConsoleAction(el("refresh"), s.resync, s.stop, s.start));
    await new Promise((r) => setTimeout(r, 0));
    assert.deepEqual(s.names(), ["resync"]);
  });
});

test("each action fires EXACTLY ONE callback — the chain is else-if, not a fallthrough", () => {
  // If any `else` were dropped, a single click would both stop a terminal and start one.
  return withDom(() => {
    for (const [action, expected] of [["stop", "stop"], ["start", "start"], ["start-fresh", "start"]]) {
      const s = spies();
      runConsoleAction(el(action, { terminalId: "t", sessionId: "s" }), s.resync, s.stop, s.start);
      assert.deepEqual(s.names(), [expected], action);
    }
  });
});
