#!/usr/bin/env node
// A check that FAILED must not render as a note.
//
// DOCTOR-L1, external review round 7. The report's glyph line read
//
//     c.code === "skipped" ? "–" : c.code === "partial" ? "~" : c.ok ? "✓" : "✗"
//
// which decides on `partial` before it ever looks at `ok`. Those fields answer different questions --
// `partial` is HOW MUCH EVIDENCE was gathered, `ok` is WHAT THE ANSWER WAS -- and they vary
// independently.
//
// SO AN OPERATOR SCANNING FOR `✗` FOUND NONE, in a run that had failed to answer the question it was
// asked. That is this repo's own `a2f9e42` false green -- no evidence is not a pass -- reappearing one
// layer up, in the rendering rather than in the verdict. Every verdict was correct the whole time.
//
// THE CASES ARE DERIVED FROM THE REAL PRODUCERS, not invented here. Two functions in this tool
// actually emit `partial`, one on each side of `ok`, and a test that made up its own objects would
// keep passing if either producer changed shape -- proving only that the glyph function is
// self-consistent. Driving the real ones means the day `clientApiKeyVerdict` stops returning a
// failing partial, this file says so instead of quietly guarding nothing. (The producer that found the
// defect, `contextWindowVerdict`, moved to `aify-env doctor` in 0.9.7, whose rows carry a state rather
// than a glyph; `client-api-key` returns the same shape when its authenticated probe cannot finish.)

import assert from "node:assert/strict";
import { test } from "node:test";

import { markFor } from "../doctor-mark.mjs";
import { clientApiKeyVerdict } from "../client-api-key-check.mjs";
import { bridgeCurrentVerdict } from "../doctor-predicates.js";

// -- the two real verdicts, one on each side of `ok` ----------------------------------------------

/** A key is required and resolved, and the probe that would prove it works never completed. */
const cappedVerdict = () => clientApiKeyVerdict({
  policy: "required", endpoint: "http://127.0.0.1:8800", hasKey: true, keySource: "the store", authed: null,
});

/**
 * Live bridges match HEAD, but one is too old to report a build at all.
 *
 * A fixture in the wrong shape makes the verdict count BOTH bridges as silent and return `unknown-all`,
 * a different row with a different meaning -- the first version of this fixture did exactly that. The
 * control below is what caught it.
 */
const HEAD_SHA = "abc1234abc1234abc1234abc1234abc1234abc12";
const partlyKnownBridges = () => bridgeCurrentVerdict({
  // Rows of `GET /bridges`: the service has already decided these are live.
  bridges: [
    { id: "bridge-a", agentId: "a", build: HEAD_SHA },
    { id: "bridge-b", agentId: "b" },
  ],
  headSha: HEAD_SHA, headShort: HEAD_SHA.slice(0, 7), bridgeCommitsSince: {},
});

test("the two producers still emit the shapes this file is about", () => {
  // POSITIVE CONTROL, and the reason the cases are derived. If either stopped producing `partial`,
  // every assertion below would still pass while guarding nothing at all.
  const capped = cappedVerdict();
  assert.equal(capped.code, "partial", "clientApiKeyVerdict no longer reports an unfinished probe as partial");
  assert.equal(capped.ok, false, "the unfinished-probe verdict is no longer a failure");

  const partly = partlyKnownBridges();
  assert.equal(partly.code, "partial", "bridgeCurrentVerdict no longer reports a mixed fleet as partial");
  assert.equal(partly.ok, true, "the mixed-fleet verdict is no longer benign");
});

test("a FAILING partial is marked as a failure", () => {
  // THE DEFECT. A capped context-window run said "this row is not a clean result" and rendered as `~`;
  // an unfinished key probe "was never proven to work" and must not wear the note either.
  assert.equal(
    markFor(cappedVerdict()), "✗",
    "a check that failed to gather its evidence is rendering as a note, so a report with no `✗` in it "
    + "can still have failed to answer the question",
  );
});

test("and a benign partial keeps its note", () => {
  // CONTRADICTION ARM. Marking every partial as a failure would satisfy the test above and cry wolf
  // on the case that is genuinely fine -- which is how a real `✗` stops being read.
  assert.equal(markFor(partlyKnownBridges()), "~");
});

// -- the rest of the vocabulary -------------------------------------------------------------------

test("a skip is not a result, and is not a failure", () => {
  // `skip()` builds {ok: true, code: "skipped"}, so this must be answered before either other field.
  assert.equal(markFor({ id: "bridge-running", ok: true, code: "skipped" }), "–");
});

test("an ordinary pass and an ordinary failure are unchanged", () => {
  assert.equal(markFor({ ok: true, code: "current" }), "✓");
  assert.equal(markFor({ ok: false, code: "stale" }), "✗");
});

test("a row with no verdict at all is not called a failure", () => {
  // `ok === false` is tested explicitly rather than `!ok`. A malformed row is a bug in whatever built
  // it; reporting it as a failed CHECK would invent a verdict nobody produced, and this tool's whole
  // argument is that an answer nobody gave must not be presented as one.
  assert.equal(markFor({ id: "x" }), "✓");
  assert.equal(markFor({}), "✓");
  assert.equal(markFor(undefined), "✓");
});
