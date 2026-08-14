// Whether a managed session still counts as ALIVE.
//
// Extracted from server.js in v0.5.4. It lived in the bridge, which no test imports, so nothing could
// reach it — the `doctor-predicates.js` lesson exactly: logic inside the bridge can only fail in
// production.
//
// BOTH DIRECTIONS ARE LOAD-BEARING, which is why the allowlist is asserted member by member rather than
// spot-checked. A status wrongly called ACTIVE leaves work queued behind a worker that is already gone;
// one wrongly called INACTIVE gets a live worker reaped out from under its run. The second is the one
// this repo has actually suffered.

import assert from "node:assert/strict";
import test from "node:test";

import { isActiveManagedSessionStatus } from "../session-predicates.mjs";

test("every ACTIVE status is recognised — the list is asserted, not sampled", () => {
  // Each of these is a real lifecycle state a managed worker passes through. Dropping any one makes a
  // worker in that state look dead: it gets reaped, and whatever it was doing is lost.
  for (const status of ["starting", "running", "recovering", "restarting"]) {
    assert.equal(isActiveManagedSessionStatus(status), true, status);
  }
});

test("terminal and unknown statuses are NOT active", () => {
  // The other direction. Treating a finished session as alive strands every message queued behind it,
  // because delivery waits for a worker that will never report again.
  for (const status of ["stopped", "failed", "exited", "completed", "dead", "offline", "unknown", "queued"]) {
    assert.equal(isActiveManagedSessionStatus(status), false, status);
  }
});

test("matching is case-insensitive and whitespace-free — statuses arrive from several writers", () => {
  // The value comes from the service, from a runtime adapter, and from local bookkeeping. A
  // case-sensitive test would call a "Running" worker dead.
  for (const status of ["Running", "RUNNING", "StArTiNg"]) {
    assert.equal(isActiveManagedSessionStatus(status), true, status);
  }
});

test("a whitespace-padded status is NOT matched — pinned as behaviour, not endorsed", () => {
  // `String(status || "").toLowerCase()` lowercases but does NOT trim. " running" is therefore inactive.
  // Asserted as-is because adding a trim would change which sessions the reaper spares, and that is a
  // behavioural decision rather than a tidy-up — but it is worth knowing the guard is this literal.
  assert.equal(isActiveManagedSessionStatus(" running"), false);
  assert.equal(isActiveManagedSessionStatus("running "), false);
});

test("absent, empty and non-string values are inactive rather than throwing", () => {
  // Sessions are read from records that may be partial, and this runs inside reaper loops — a throw
  // there takes down the sweep for every other agent too.
  for (const value of [undefined, null, "", 0, false, NaN]) {
    assert.equal(isActiveManagedSessionStatus(value), false, JSON.stringify(value));
  }
  assert.doesNotThrow(() => isActiveManagedSessionStatus({}));
  assert.doesNotThrow(() => isActiveManagedSessionStatus([]));
});
