// D10: a check that could not run reported the same as one that ran and passed.
//
// `skip()` builds `{ok: true, code: "skipped"}`. The HUMAN report has always been honest about that
// -- `markFor` answers "–" before it looks at `ok` -- so the defect lived entirely in `--json`, which
// is the surface an INSTALLING AGENT parses. It keys on `.ok`, saw `true`, and could not tell "this
// passed" from "this never ran".
//
// WHY IT MATTERED RATHER THAN BEING TIDY. D11 has the doctor resolving the API key only from the repo
// checkout, so an agent running `aify-comms doctor` from its own working directory loses every
// service-reading check to a skip. Both defects together are how "I could not ask" was reported as
// "no bridge is online". Neither alone would have produced that sentence.
//
// TESTED AS A MODULE because importing `doctor.js` RUNS the doctor: it is a script that executes its
// checks at import and ends in `process.exit()`. The verdict-shaping is the part most worth testing
// and the part least worth standing up a fleet for.

import { test } from "node:test";
import assert from "node:assert/strict";

import { buildReport, summaryLine, isSkipped } from "../doctor-report.mjs";
import { markFor } from "../doctor-mark.mjs";

/** As `skip()` builds it. */
const skipped = (id) => ({ id, ok: true, code: "skipped", detail: "process inspection is Linux-only" });
/** As a PREDICATE builds it, through `add()` -- the second producer, which never touches `skip()`. */
const predicateSkip = (id) => ({ id, ok: true, code: "skipped", detail: "no live bridge reported a build" });
const passing = (id) => ({ id, ok: true, code: "current", detail: "up to date" });
const failing = (id) => ({ id, ok: false, code: "stale", detail: "behind HEAD", fix: "reinstall" });

test("THE REPORT COUNTS REAL CHECKS AT ALL", () => {
  // POSITIVE CONTROL. Every assertion below is about a count being SMALLER than the naive one, and an
  // empty report satisfies all of them while proving the opposite.
  const r = buildReport([passing("a"), passing("b"), failing("c")], { serviceUrl: "http://x" });
  assert.equal(r.passed, 2);
  assert.equal(r.failed, 1);
  assert.equal(r.skipped, 0);
  assert.equal(r.ok, false);
  assert.equal(r.service_url, "http://x");
});

test("A SKIPPED CHECK IS NOT COUNTED AS PASSED", () => {
  const r = buildReport([passing("a"), skipped("bridge-running"), skipped("agent-identity")]);
  assert.equal(r.passed, 1, "a skip was counted as a pass");
  assert.equal(r.skipped, 2);
  assert.equal(r.failed, 0);
});

test("its row reads ok:false, so a consumer keying on `.ok` cannot read a pass", () => {
  // The actual defect. The count above is what a careful reader sees; this is what every naive one
  // sees, and the naive read is the one that produced a wrong sentence in an agent's summary.
  const r = buildReport([skipped("env-bridge")]);
  const row = r.checks[0];
  assert.equal(row.ok, false, "a skipped check still reports ok:true in the JSON");
  assert.equal(row.skipped, true, "the row does not say it was skipped");
  assert.equal(row.code, "skipped", "the original code was lost");
});

test("BOTH PRODUCERS ARE NORMALISED, not just the skip() helper", () => {
  // `bridgeCurrentVerdict` returns `code: "skipped"` through `add()` and never touches `skip()`.
  // Deriving from a flag the helper sets would have fixed one path and left the other lying -- and
  // the predicate path is the one nobody would have thought to check.
  const r = buildReport([predicateSkip("bridge-current"), skipped("bridge-running")]);
  assert.equal(r.skipped, 2, "a predicate-built skip was not recognised");
  assert.deepEqual(r.checks.map((c) => c.ok), [false, false]);
});

test("--STRICT IS UNCHANGED: skips alone do not make the run fail", () => {
  // The constraint that rules out the obvious fix. On Windows `bridge-running` and `agent-identity`
  // ALWAYS skip, so treating a skip as a failure turns every ordinary Windows run red -- a worse lie
  // than the one being fixed, dressed up as strictness.
  const r = buildReport([passing("a"), skipped("bridge-running"), skipped("agent-identity")]);
  assert.equal(r.ok, true, "skips made the run fail, which would redden every Windows run");

  // AND THE SAME FOR A SKIP THAT ALREADY READS ok:false, which is the case that actually bites and
  // the one this test MISSED until a mutation said so. Removing `!isSkipped(c)` from the failed
  // filter left every assertion here green, because the fixture above carries `ok: true` and so
  // never reached the clause being deleted. The shape below is what `buildReport` itself emits, so
  // it arrives here the moment anyone feeds a report back in or "fixes" `skip()` to build ok:false.
  const normalised = { id: "bridge-running", ok: false, skipped: true, code: "skipped", detail: "Linux-only" };
  const again = buildReport([passing("a"), normalised]);
  assert.equal(again.ok, true,
    "an ok:false skip failed the run -- the exclusion is keyed on `ok` rather than on `code`");
  assert.equal(again.failed, 0);
  assert.equal(again.skipped, 1);
});

test("a real failure still fails, beside any number of skips", () => {
  const r = buildReport([failing("service"), skipped("bridge-running")]);
  assert.equal(r.ok, false);
  assert.equal(r.failed, 1);
});

test("THE HUMAN GLYPH IS UNCHANGED, because markFor answers on `code` first", () => {
  // The renderer walks the raw checks, not the normalised ones, and answers on `code` before `ok` --
  // so flipping `ok` cannot turn a "–" into a "✗". Asserted against the normalised row too, since
  // that is the shape most likely to be passed here by mistake later.
  assert.equal(markFor(skipped("bridge-running")), "–");
  assert.equal(markFor(buildReport([skipped("x")]).checks[0]), "–");
});

test("the summary NAMES what was not verified", () => {
  // "All checks passed." was printed after runs where several checks never ran. A reader has to be
  // told what was not verified, not merely that nothing broke.
  const clean = summaryLine(buildReport([passing("a"), passing("b")]));
  assert.match(clean, /2 check\(s\) passed\./);
  assert.ok(!/skipped/.test(clean), "a run with no skips mentions skipping");

  const partial = summaryLine(buildReport([passing("a"), skipped("b"), skipped("c")]));
  assert.match(partial, /1 check\(s\) passed\./, "the passed count still includes the skips");
  assert.match(partial, /2 skipped, so NOT verified here\./);

  const bad = summaryLine(buildReport([failing("a"), skipped("b")]));
  assert.match(bad, /1 check\(s\) need attention\./);
  assert.match(bad, /1 skipped, so NOT verified here\./);
});

test("isSkipped is answered by `code`, which is the one signal both producers set", () => {
  assert.equal(isSkipped({ code: "skipped" }), true);
  assert.equal(isSkipped({ ok: true, code: "current" }), false);
  assert.equal(isSkipped(undefined), false, "a malformed row must not be read as skipped");
});
