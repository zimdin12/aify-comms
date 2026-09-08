#!/usr/bin/env node
// The console-latency collector's own arithmetic, driven by the counterexamples that broke it.
//
// AN INDEPENDENT REVIEW FOUND TWO DEFECTS IN THIS LOGIC by writing throwaway scripts against a copy
// of it, and both produced CONFIDENT WRONG NUMBERS rather than errors -- which is the worst shape a
// measuring instrument can fail in, because the output looks exactly like a result.
//
//   REPEATED CONTENT. `indexOf` returns the FIRST match. A stream whose true offset was 0 and whose
//   true lag was 50ms was reported at offset -300 and 350ms, with 300 pairs behind it. Agent output
//   repeats by nature: spinners, progress bars, retried lines.
//
//   SKIPPED MISMATCHES. Units that disagreed were passed over, so one run dropped 200 of them and
//   reported 2,300 pairs at a confident 50ms. A disagreeing unit means the alignment is wrong or the
//   streams are not the same output.
//
// THEY ARE TESTS RATHER THAN SCRATCH FILES BECAUSE OF WHERE THEY WERE FOUND. Nothing in
// `measure-console-latency.mjs` could be exercised without a live daemon and a live service, so the
// only way to reach this arithmetic was to copy it -- and a defect fixed in a copy has nothing
// stopping it coming back. The pairing now lives in its own module and this drives it.

import assert from "node:assert/strict";
import path from "node:path";
import { test } from "node:test";

const HERE = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const MODULE = path.join(HERE, "..", "..", "..", "scripts", "console-latency-pairing.mjs");
const { alignment, distribution, pairLags, MISMATCH_LIMIT, PROBE_MIN } = await import(`file://${MODULE.replace(/\\/g, "/")}`);

/** A stream of `count` distinct lines, so every window of it is unique. */
function uniqueStream(count, tag = "L") {
  let text = "";
  for (let i = 0; i < count; i += 1) text += `${tag}${String(i).padStart(6, "0")} the quick brown fox\n`;
  return text;
}

/** Timestamps for every unit of `text`, the producer at `base` and the consumer `lag` ms later. */
function clocks(text, { base = 1000, step = 0 } = {}) {
  return Array.from({ length: text.length }, (_, i) => base + i * step);
}

test("POSITIVE CONTROL: unique content aligns and reports the real lag", () => {
  // Everything below is a refusal. Without this, a module that refused every input would satisfy the
  // whole file and the collector would never produce a number again.
  const produced = uniqueStream(200);
  const delivered = produced;
  const anchor = alignment(produced, delivered);
  assert.equal(anchor.problem, undefined, `alignment refused a clean stream: ${anchor.problem}`);
  assert.equal(anchor.offset, 0);

  const paired = pairLags({
    produced,
    delivered,
    producedAt: clocks(produced, { base: 1000 }),
    deliveredAt: clocks(delivered, { base: 1050 }),
    offset: anchor.offset,
  });
  assert.equal(paired.problem, "");
  assert.equal(paired.mismatched, 0);
  const stats = distribution(paired.lags);
  assert.equal(stats.problem, "");
  assert.equal(stats.p50, 50, "the real 50ms lag was not recovered");
  assert.ok(stats.count > 2000, `only ${stats.count} units paired`);
});

test("REPEATED CONTENT IS REFUSED, rather than anchored on the wrong occurrence", () => {
  // THE MEASURED COUNTEREXAMPLE. True offset 0, true lag 50ms; `indexOf` alone found the first of
  // many identical windows and produced offset -300 and 350ms, with 300 pairs of apparent evidence.
  const repeated = "spinner .. working ..\n".repeat(400);
  const anchor = alignment(repeated, repeated);
  assert.ok(anchor.problem, `a repeating stream was anchored at offset ${anchor.offset}`);
  assert.match(anchor.problem, /unique/);
});

test("A PROBE THAT GROWS INTO UNIQUENESS IS ACCEPTED, so repetition alone is not fatal", () => {
  // The refusal above must not become "any stream with a repeated line is unmeasurable". A short
  // window repeats; a longer one containing a distinct marker does not, and the probe grows until it
  // either becomes unique or runs out of room.
  // BUILT SO THE FIRST PROBE IS NOT ENOUGH, which is the only version of this test that proves
  // anything. The middle of the stream sits 180 units before the marker: a 120-unit window from
  // there is pure repetition and matches everywhere, and only the grown window reaches the marker
  // and becomes unique. A mutant that stopped the probe growing survived a version where the first
  // window already contained it, and the two assertions below are what make that impossible.
  const marker = "UNIQUE-MARKER-A";
  const noisy = `${".".repeat(2200)}${marker}${".".repeat(2200 - marker.length - 360)}`;
  const middle = Math.floor(noisy.length / 2);
  assert.equal(2200 - middle, 180, "the fixture no longer places the marker out of the first probe's reach");
  const firstProbe = noisy.slice(middle, middle + PROBE_MIN);
  assert.notEqual(noisy.indexOf(firstProbe), noisy.lastIndexOf(firstProbe),
    "the first probe is already unique, so this fixture does not exercise the growth at all");

  const anchor = alignment(noisy, noisy);
  assert.equal(anchor.problem, undefined, `a stream with one distinct marker was refused: ${anchor.problem}`);
  assert.equal(anchor.offset, 0);
  assert.ok(anchor.probeSize > PROBE_MIN, `the anchor was found at the first size (${anchor.probeSize})`);
});

test("STREAMS THAT SHARE NOTHING ARE REFUSED, and say so differently", () => {
  // A different reason with a different remedy: no anchor at all means the two subscriptions are not
  // describing the same process, which is a wiring mistake rather than a content one.
  const anchor = alignment(uniqueStream(200, "A"), uniqueStream(200, "B"));
  assert.match(anchor.problem, /share no anchor/);
});

test("MISMATCHED UNITS ARE COUNTED AND REFUSE THE RUN, never skipped", () => {
  // THE SECOND MEASURED COUNTEREXAMPLE: 200 dropped mismatches and a confident report of 2,300 pairs
  // at 50ms. A unit that disagrees is evidence the alignment is wrong; passing over it converts that
  // evidence into a cleaner-looking result.
  const produced = uniqueStream(200);
  const delivered = `${produced.slice(0, 1000)}${"Z".repeat(400)}${produced.slice(1400)}`;
  const paired = pairLags({
    produced,
    delivered,
    producedAt: clocks(produced, { base: 1000 }),
    deliveredAt: clocks(delivered, { base: 1050 }),
    offset: 0,
  });
  assert.ok(paired.mismatched > 100, `only ${paired.mismatched} mismatches were noticed`);
  assert.ok(paired.problem, "a run with hundreds of disagreeing units reported no problem");
  assert.match(paired.problem, /disagreed/);
});

test("A FEW MISMATCHES DO NOT REFUSE A RUN, because the threshold is a share and not a count", () => {
  // The boundary matters in both directions. One flipped unit in a long stream is a decode edge or a
  // frame boundary, and refusing on it would make the collector unusable; hundreds are a wrong
  // alignment. The line is drawn as a proportion, and this pins which side each lands on.
  const produced = uniqueStream(400);
  const delivered = `${produced.slice(0, 500)}Z${produced.slice(501)}`;
  const paired = pairLags({
    produced,
    delivered,
    producedAt: clocks(produced, { base: 1000 }),
    deliveredAt: clocks(delivered, { base: 1050 }),
    offset: 0,
  });
  assert.equal(paired.mismatched, 1);
  assert.equal(paired.problem, "", "one disagreeing unit in thousands refused the whole run");
  assert.ok(MISMATCH_LIMIT > 0 && MISMATCH_LIMIT < 1, "the threshold has been set to a value that refuses everything or nothing");
});

test("UNITS OUTSIDE THE OVERLAP ARE NOT MISMATCHES", () => {
  // The two streams are joined at different moments, so their ends do not line up. Counting those as
  // disagreements would refuse every honest run -- the two failures have different causes and
  // different remedies, so they are counted separately.
  // The consumer joined 500 units late and kept reading 300 units past where the producer's
  // subscription was cut, which is the ordinary shape of two subscriptions opened and closed by
  // hand. Every unit in between agrees.
  const produced = uniqueStream(200);
  const delivered = produced.slice(500) + "T".repeat(300);
  const paired = pairLags({
    produced,
    delivered,
    producedAt: clocks(produced, { base: 1000 }),
    deliveredAt: clocks(delivered, { base: 1050 }),
    offset: 500,
  });
  assert.equal(paired.unpairable, 300, "the consumer's own tail was not counted as outside the overlap");
  assert.equal(paired.mismatched, 0, "units off the end of the overlap were counted as disagreements");
  assert.equal(paired.problem, "");
  assert.equal(paired.lags.length, produced.length - 500);
});

test("TOO FEW PAIRS REPORTS NO DISTRIBUTION, rather than a percentile of nine samples", () => {
  assert.match(distribution([1, 2, 3]).problem, /too few/);
  assert.match(distribution(null).problem, /too few/);
  // POSITIVE CONTROL: enough samples does produce one.
  assert.equal(distribution(Array.from({ length: 200 }, () => 42)).p50, 42);
});

test("THE DISTRIBUTION DOES NOT REORDER THE CALLER'S ARRAY", () => {
  // It sorts to find percentiles. Sorting in place would silently destroy the time ordering the
  // caller still holds, and the next thing computed from it would be about a different sequence.
  const lags = [9, 1, 5];
  distribution(lags, { minimum: 1 });
  assert.deepEqual(lags, [9, 1, 5]);
});
