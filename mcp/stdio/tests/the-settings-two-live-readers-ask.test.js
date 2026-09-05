#!/usr/bin/env node
// Two live modules whose only test coverage rode along in files the v0.6.2 residue deletion removed.
//
// WHY THIS FILE EXISTS AND WHAT IT REPLACES. Deleting the retired environment-bridge tier took 58
// files with it, and two of the test files in that set were also the ONLY tests naming
// `delegation-setting.mjs` and `env-listing.mjs` — both of which are live: `doctor-predicates.js`
// reads the first to decide where spawns run, and `env-processes-check.mjs` reads the second to list
// what a host is running. `every-module-is-imported-by-a-test` and `every-export-is-named-by-a-test`
// both went red on the deletion and are the reason this exists rather than the loss going unnoticed.
//
// The gates were right to fire. A deletion that quietly reduces coverage of code that SURVIVES is
// the expensive kind, because the tree still looks tested.

import assert from "node:assert/strict";
import test from "node:test";

import { AFFIRMATIVE, delegationOptedIn } from "../delegation-setting.mjs";
import { envListing } from "../env-listing.mjs";

test("delegation is opted into ONLY by a spelling somebody declared", () => {
  // The cost of being wrong is asymmetric and the module says so: turning a host's spawning over to
  // another daemon on the strength of an unrecognised string is the more expensive way to be wrong.
  for (const yes of AFFIRMATIVE) {
    assert.equal(delegationOptedIn(yes), true, `${yes} is a declared affirmative`);
    assert.equal(delegationOptedIn(` ${yes.toUpperCase()} `), true, "case and padding are not meaning");
  }
});

test("absence, blankness and an UNDECLARED spelling are all off", () => {
  // `maybe`, a typo and a half-finished edit are the realistic inputs, and each must fail closed.
  for (const no of [undefined, null, "", "   ", "0", "false", "no", "off", "maybe", "ture", "y"]) {
    assert.equal(delegationOptedIn(no), false, `${JSON.stringify(no)} must not opt a host in`);
  }
});

test("AFFIRMATIVE is the whole vocabulary, so a reader cannot invent a second one", () => {
  // The module exists because there were FOUR readers and TWO truth functions. The exported list is
  // what stops a fifth reader spelling the answer itself.
  assert.deepEqual(AFFIRMATIVE, ["1", "true", "yes", "on"]);
});

test("a REFUSED listing is not an empty one", () => {
  // The distinction this module was written for: a host that refused to answer and a host running
  // nothing are different facts, and reporting the first as the second is how an unreachable
  // environment reads as a healthy idle one.
  assert.deepEqual(envListing({ ok: false }), { processes: null, refused: true });
});

test("an UNREADABLE shape is not an empty listing either", () => {
  // Neither a refusal nor a list: nothing was learned, and `processes: null` says so.
  for (const shape of [undefined, null, {}, { handle: {} }, { handle: { processes: "no" } }, 7]) {
    const listing = envListing(shape);
    assert.equal(listing.processes, null, `${JSON.stringify(shape)} should yield no listing`);
    assert.equal(listing.refused, false, "only an explicit refusal is a refusal");
  }
});

test("both shapes a host can answer with are read the same way", () => {
  // The caller should not have to know whether it got a bare array or a `processes` key.
  const rows = [{ pid: 1 }, { pid: 2 }];
  assert.deepEqual(envListing(rows), { processes: rows, refused: false });
  assert.deepEqual(envListing({ handle: rows }), { processes: rows, refused: false });
  assert.deepEqual(envListing({ handle: { processes: rows } }), { processes: rows, refused: false });
  assert.deepEqual(envListing({ processes: rows }), { processes: rows, refused: false });
});

test("an EMPTY listing is a real answer, distinct from no listing", () => {
  // The control for the two tests above: a host that answered "nothing running" must not be reported
  // as one that could not be read.
  const listing = envListing({ handle: { processes: [] } });
  assert.deepEqual(listing.processes, []);
  assert.equal(listing.refused, false);
  assert.notEqual(listing.processes, null, "an empty list collapsed into `could not tell`");
});
