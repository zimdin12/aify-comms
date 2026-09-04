#!/usr/bin/env node
// A loop that guards re-entry but not SHUTDOWN is the pre-fix shape, and nothing checked for it.
//
// `loop-gate.mjs` exists because all fourteen bridge loop gates were missing one term.
// `shutdownWithStatus()` is async: it sets `shutdownStarted = true` and then awaits a great deal of
// work — a 1500ms race on `reportResidentLost`, `reportEnvironmentOffline()`,
// `TERMINAL_MANAGER.stopAll()`, `runManagedTeardownForBridge()`, four session-shutdown passes. The
// loop timers keep firing throughout, and not one gate consulted the flag. So a bridge would report
// itself OFFLINE and go on CLAIMING work for seconds afterwards: spawn requests, dispatch runs,
// terminal controls. Each claim is taken by a process about to exit and never executed, leaving the
// run claimed-but-orphaned until the service's aging backstop requeues it minutes later. The
// observable symptom is a restart that produces no worker — one of the most-repeated failures in
// this project's history.
//
// `loop-gate.test.js` proves the DECISION is right: the full truth table, both directions of every
// term, a throw on a missing or non-boolean one. It cannot prove every loop ASKS. That is the gap
// this file covers, and it is the same caller-side gap found in the dashboard's injection contract —
// a correct guard nobody calls guards nothing.
//
// THE STRUCTURAL RULE: a module that declares a re-entry flag (`somethingBusy`) is running a loop, and
// a loop must consult the shutdown gate. All five such modules comply today; the fix is complete and
// this pins it. A new loop module is the realistic regression — it will copy the busy-flag idiom from
// a sibling, and the shutdown term is the one that was missing from all fourteen the first time.

import assert from "node:assert/strict";

import { bridgeSources } from "./bridge-sources.mjs";

const BUSY_FLAG = /^let\s+([A-Za-z_$][\w$]*[Bb]usy)\s*=/gm;
const GATE_CALL = /shouldSkipLoop\s*\(/g;

const modules = bridgeSources().map(([file, raw]) => {
  const src = raw.replace(/\r\n/g, "\n");
  return {
    file,
    busy: [...src.matchAll(BUSY_FLAG)].map((m) => m[1]),
    gateCalls: [...src.matchAll(GATE_CALL)].length,
  };
});

// ── the population is real ───────────────────────────────────────────────────────────────────
{
  const withBusy = modules.filter((m) => m.busy.length);
  // ONE, NOT FIVE, SINCE v0.6.2. Four of the five re-entry-guarded loops were the environment
  // bridge's -- spawn, environment-control, terminal-control and managed-env-sync -- and went with
  // it. `dispatch-loop.mjs` is the one a resident still runs.
  //
  // THE FLOOR IS KEPT RATHER THAN DROPPED, because its job is unchanged: every assertion below is
  // satisfied by an empty population, so a scan that stopped matching the pattern would make this
  // gate pass loudest exactly when it had broken. Lowered to the real number, and it may only rise.
  assert.ok(
    withBusy.length >= 1,
    `only ${withBusy.length} modules declare a re-entry flag — the pattern was renamed and this gate `
      + "is now scanning for something that no longer exists",
  );
  const totalCalls = modules.reduce((n, m) => n + m.gateCalls, 0);
  // FOURTEEN BEFORE v0.6.2, four after: the environment bridge owned the other ten, across its four
  // loops and the ensure* wrappers that armed them. Same floor, re-measured against the code that
  // remains, and it may only rise.
  assert.ok(totalCalls >= 4, `only ${totalCalls} shouldSkipLoop call sites; there were four gates`);
}

// ── every re-entry-guarded loop also consults the shutdown gate ──────────────────────────────
{
  const ungated = modules.filter((m) => m.busy.length && m.gateCalls === 0);
  assert.deepEqual(
    ungated.map((m) => `${m.file} (${m.busy.join(", ")})`),
    [],
    "these modules guard re-entry but never ask whether the bridge is shutting down. During the async "
      + "teardown window their timers keep firing and they CLAIM work the process will never execute — "
      + "the claimed-but-orphaned run behind 'restart produced no worker'. Route the tick through "
      + "`shouldSkipLoop({ eligible, alreadyActive, shuttingDown: shutdownStarted })`.",
  );
}

// ── the known loop modules each still carry their gate ───────────────────────────────────────
{
  // An exact set, not a count: a module dropping its gate while another gains one would keep any
  // total unchanged.
  // FOUR NAMES LEFT THIS LIST IN v0.6.2 -- environment-control-loop, managed-environment-sync,
  // spawn-loop and terminal-control-loop were the environment bridge's and were deleted with it.
  // The list is still exact rather than a count, for the reason above.
  const expected = [
    "dispatch-loop.mjs",
  ];
  for (const file of expected) {
    const found = modules.find((m) => m.file === file);
    assert.ok(found, `${file} is gone — if the loop moved, update this list to its new home`);
    assert.ok(found.gateCalls > 0, `${file} no longer calls shouldSkipLoop`);
    assert.ok(found.busy.length > 0, `${file} lost its re-entry flag; is it still a loop?`);
  }
}

// ── the shutdown term is passed, not just the function called ────────────────────────────────
{
  // Calling the gate with `shuttingDown: false` hardcoded would satisfy every check above while
  // restoring the exact defect. Each caller must read the real flag.
  //
  // THE SET IS DERIVED, not matched by filename. My first version selected `*-loop.mjs`, which swept
  // in `hermes-delivery-loop.mjs` — a per-agent delivery loop that runs inside
  // `hermes-managed-host.js`, a different process with no bridge timers and no `shutdownStarted` to
  // read. It is correctly outside this invariant, and a name-shaped filter said otherwise. Selecting
  // on "calls the gate" asks the question that actually matters.
  const callers = bridgeSources()
    .map(([file, raw]) => [file, raw.replace(/\r\n/g, "\n")])
    .filter(([file, src]) => file !== "loop-gate.mjs" && /shouldSkipLoop\s*\(/.test(src));
  // FIVE BEFORE v0.6.2, two after -- the bridge owned three of them. The floor stays because an
  // empty caller set satisfies every per-caller assertion below for free.
  assert.ok(callers.length >= 2, `only ${callers.length} modules call the gate`);
  for (const [file, src] of callers) {
    assert.match(
      src,
      /shuttingDown:\s*shutdownStarted/,
      `${file} calls the gate without passing the real shutdownStarted flag — a hardcoded false here `
        + "restores the defect while passing every other check in this file",
    );
  }
}

console.log("every-loop-consults-the-shutdown-gate.test.js: all assertions passed");
