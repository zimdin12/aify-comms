// When an environment cannot open a terminal, the card says why.
//
// THIS IS THE OTHER HALF OF A FIX I MADE AN HOUR EARLIER, and without it that fix was a trade rather
// than an improvement.
//
// The bridge used to advertise `terminal: true` from its own node-pty. Since v0.6 Phase 8 the bridge
// is not the tier that opens anything -- aify-env is -- so with aify-env down, the environment still
// read `online` with a terminal, twenty managed agents read `available`, and every send to them would
// have failed. Correcting that makes those agents read `offline`, which is TRUE and says nothing at
// all about why. An operator would go hunting a delivery bug: the same wrong hunt, one tier over.
//
// So the reason travels with the answer, from the tier that produced it, to the card an operator
// looks at when an environment misbehaves.

import assert from "node:assert/strict";
import test from "node:test";

import { terminalReasonNote, unknownProcessBadge } from "./environments-panels.mjs";

const env = (over = {}) => ({ id: "windows:host:default", terminal: true, metadata: {}, ...over });

test("an environment that CAN open a terminal says nothing", () => {
  // A card explaining why everything is fine is noise, and noise is what stops the line being read on
  // the day it matters.
  assert.equal(terminalReasonNote(env({ terminal: true, metadata: { terminalReason: "all good" } })), "");
});

test("an environment that cannot says why", () => {
  const html = terminalReasonNote(env({
    terminal: false,
    metadata: { terminalReason: "spawns are delegated and aify-env did not answer" },
  }));
  assert.match(html, /No terminal/);
  assert.match(html, /aify-env did not answer/);
});

test("a reason that is missing renders nothing rather than 'unknown'", () => {
  // A bridge too old to send one has not given a reason. Inventing a word here would put it in that
  // bridge's mouth, and the operator would act on a sentence nothing produced.
  for (const metadata of [{}, { terminalReason: "" }, { terminalReason: "   " }, undefined]) {
    assert.equal(terminalReasonNote(env({ terminal: false, metadata })), "");
  }
});

test("only an explicit false counts as 'cannot'", () => {
  // `undefined` is an older bridge that does not report the field at all; treating that as a refusal
  // would put a red note on every environment that predates this change.
  assert.equal(terminalReasonNote({ id: "x", metadata: { terminalReason: "why" } }), "");
  assert.equal(terminalReasonNote({ id: "x", terminal: null, metadata: { terminalReason: "why" } }), "");
});

test("the reason is escaped", () => {
  // It arrives over HTTP from another process. Nothing about its origin makes it safe markup.
  const html = terminalReasonNote(env({
    terminal: false,
    metadata: { terminalReason: '<img src=x onerror="alert(1)">' },
  }));
  assert.ok(!html.includes("<img"), "an environment's reason reached the DOM as markup");
  assert.match(html, /&lt;img/);
});

test("a malformed environment does not throw", () => {
  // This runs inside the render for every card. One bad row must not blank the page.
  for (const value of [null, undefined, {}, { terminal: false }, { terminal: false, metadata: null }]) {
    assert.doesNotThrow(() => terminalReasonNote(value));
  }
});


// ---- and what it cannot SEE ---------------------------------------------------------------
//
// The operator watched a live PTY under aify-env -- `claude-aify --aify-agent ef-manager`, pid
// 155844 -- that no screen would display, and asked for exactly this: "aify-env side running
// process visibility, to catch orphans like that". The bridge can count them on every heartbeat at
// no extra cost, because aify-env's /health carries the process list already.

test("an environment running processes the bridge does not know about says how many", () => {
  const html = unknownProcessBadge({ metadata: { unknownProcesses: 2 } });
  assert.match(html, /2 unaccounted processes/);
});

test("one is singular, because a badge reading \"1 unaccounted processes\" is a badge nobody trusts", () => {
  assert.match(unknownProcessBadge({ metadata: { unknownProcesses: 1 } }), /1 unaccounted process</);
});

test("ZERO renders nothing, and so does ABSENT — but they are different facts", () => {
  // A bridge that reached aify-env and accounts for everything reports 0. A bridge that could not
  // ask reports nothing at all. Neither draws a badge, and the difference matters to what the card
  // does NOT claim: "0 unaccounted" for the second would be asserting knowledge nobody has.
  assert.equal(unknownProcessBadge({ metadata: { unknownProcesses: 0 } }), '');
  assert.equal(unknownProcessBadge({ metadata: { unknownProcesses: null } }), '');
  assert.equal(unknownProcessBadge({ metadata: {} }), '');
  assert.equal(unknownProcessBadge({}), '');
  assert.equal(unknownProcessBadge(null), '');
});

test("a non-number is not a count", () => {
  // It arrives over HTTP from another process. `"3"` would render, and `NaN` would render as a
  // badge saying NaN, which is worse than saying nothing.
  for (const value of ['3', NaN, Infinity, true, [], {}]) {
    assert.equal(unknownProcessBadge({ metadata: { unknownProcesses: value } }), '',
      `${JSON.stringify(value)} was rendered as a count`);
  }
});

test("the badge does not throw on a malformed environment", () => {
  // One bad row must not blank the whole environments page.
  for (const value of [undefined, { metadata: null }, { metadata: 'x' }]) {
    assert.doesNotThrow(() => unknownProcessBadge(value));
  }
});
