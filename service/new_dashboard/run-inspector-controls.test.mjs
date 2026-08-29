// Real tests for the run inspector's capability matrix.
//
// `runInspectorCapabilities` decides what the operator may DO to a run, and each of its six flags is wrong
// in a direction that matters: offering Steer on a finished run sends input nowhere, withholding Close on a
// stuck one leaves no way to clear it, and offering Open console without a session opens an empty panel.
// It is a pure function and had no test while it lived in app.js.
//
// SEALING. `state` is a shared singleton — `sessionForRun` reads `state.sessions` — so it is rebuilt per
// test.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import {
  renderRunInspectorControls,
  runInspectorCapabilities,
  sessionForRun,
} from "./run-inspector-controls.mjs";

const run = (extra = {}) => ({ id: "r1", targetAgentId: "coder", ...extra });

function caps(runObj, sessions = []) {
  state.sessions = sessions;
  state.agents = [];
  return runInspectorCapabilities(runObj);
}

test("an ACTIVE run can be steered and interrupted", () => {
  for (const status of ["claimed", "running"]) {
    const c = caps(run({ status }));
    assert.equal(c.steer, true, `${status} must be steerable`);
    assert.equal(c.interrupt, true, `${status} must be interruptible`);
  }
});

test("a TERMINAL run can be neither steered nor interrupted", () => {
  // Steering a finished run sends the operator's input nowhere and reports success.
  for (const status of ["completed", "failed", "cancelled"]) {
    const c = caps(run({ status }));
    assert.equal(c.steer, false, `${status} must not be steerable`);
    assert.equal(c.interrupt, false, `${status} must not be interruptible`);
  }
});

test("Close is offered until the run reaches a terminal state", () => {
  // The escape hatch for a stuck run. Withholding it on a live-but-wedged run leaves no way to clear it.
  for (const status of ["queued", "claimed", "running"]) {
    assert.equal(caps(run({ status })).close, true, `${status} must be closable`);
  }
  for (const status of ["completed", "failed", "cancelled"]) {
    assert.equal(caps(run({ status })).close, false, `${status} is already over`);
  }
});

test("Close needs a run id, not just a non-terminal status", () => {
  assert.equal(caps({ status: "running" }).close, false, "there is nothing to address the close to");
});

test("retry and queue-after need a TARGET, whatever the status", () => {
  // Both send new work to an agent. Without a target there is nobody to send it to.
  const withTarget = caps(run({ status: "failed" }));
  assert.equal(withTarget.retry, true);
  assert.equal(withTarget.queueAfter, true);

  const noTarget = caps({ id: "r1", status: "failed" });
  assert.equal(noTarget.retry, false);
  assert.equal(noTarget.queueAfter, false);
});

test("Open console is offered only when the run resolves to a session", () => {
  const none = caps(run({ status: "running" }), []);
  assert.equal(none.openConsole, false, "no session means the console would open empty");

  const withSession = caps(run({ status: "running" }), [{ id: "s1", agentId: "coder" }]);
  assert.equal(withSession.openConsole, true);
});

test("an unknown or missing status is treated as neither active nor terminal", () => {
  // Fail-safe direction: a status the resolver does not recognise must not enable Steer, but must still
  // allow Close — an unrecognisable run is exactly the one an operator needs to be able to clear.
  for (const runObj of [run({ status: "some-future-status" }), run({}), run({ status: null })]) {
    const c = runInspectorCapabilities(runObj);
    assert.equal(c.steer, false);
    assert.equal(c.close, true);
  }
});

test("sessionForRun resolves through the run's target agent", () => {
  state.sessions = [{ id: "s1", agentId: "coder" }];
  state.agents = [];
  assert.equal(sessionForRun(run())?.id, "s1");
  assert.equal(sessionForRun({ id: "r2" }), null, "a run with no target has no session");
});

test("the controls row is STABLE — every button always renders, disabled reflects capability", () => {
  // My first version asserted that a finished run omits Steer. It does not: all six buttons always render
  // and capability is expressed with `disabled`. That is the better behaviour and the one worth pinning —
  // buttons appearing and disappearing would move the row under the operator's cursor between polls.
  state.sessions = [];
  state.agents = [];
  const finished = renderRunInspectorControls(run({ status: "completed" }));
  for (const control of ["steer", "interrupt", "queue-after", "retry", "close", "open-console"]) {
    assert.ok(finished.includes(`data-run-control="${control}"`), `${control} must always be present`);
  }
  assert.match(finished, /data-run-control="steer" disabled/, "steer must be disabled on a finished run");
  assert.match(finished, /data-run-control="close" disabled/, "…and so must close");

  state.sessions = [{ id: "s1", agentId: "coder" }];
  const live = renderRunInspectorControls(run({ status: "running" }));
  assert.ok(!/data-run-control="steer" disabled/.test(live), "a live run must have steer ENABLED");
  assert.ok(!/data-run-control="open-console" disabled/.test(live),
    "…and open-console, because this run resolves to a session");
});

test("open-console is disabled when the run has no session, even while live", () => {
  state.sessions = [];
  state.agents = [];
  const live = renderRunInspectorControls(run({ status: "running" }));
  assert.match(live, /data-run-control="open-console" disabled/,
    "an enabled button that opens an empty panel is worse than a disabled one");
});
