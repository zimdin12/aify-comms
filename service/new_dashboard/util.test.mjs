// Unit tests for the shared pure utilities — including the three that had NEVER been tested.
//
// `util.js` has said "No DOM, no module-level mutable state — safe to import anywhere and unit-test
// directly" since the Phase 0.1 module split, and nothing tested it. `esc` in particular is the escaper
// every interpolation in the dashboard goes through: 251 call sites in app.js alone, guarding against
// stored XSS, and its behaviour rested on a comment. A true claim with no enforcement is how a true claim
// becomes a stale one.
//
// The three formatters below arrived from app.js in v0.5.4, which could not test them at all — importing
// app.js runs module-scope browser code and throws before a test can reach anything.

import assert from "node:assert/strict";
import { test } from "node:test";

import { esc, fileSizeLabel, relTime, tsMs, usageFmtTokens, usageResetLabel } from "./util.js";

// ---------------------------------------------------------------- esc (previously untested)

test("esc escapes all five characters that can break out of text or a quoted attribute", () => {
  assert.equal(esc(`&<>"'`), "&amp;&lt;&gt;&quot;&#39;");
});

test("esc neutralises a script tag and an attribute-breaking payload", () => {
  assert.equal(esc("<script>alert(1)</script>"), "&lt;script&gt;alert(1)&lt;/script&gt;");
  // The attribute case: a bare quote must not be able to close the attribute it sits in.
  assert.doesNotMatch(esc('" onerror="boom()'), /"/);
});

test("esc renders null and undefined as empty, not as the words", () => {
  assert.equal(esc(null), "");
  assert.equal(esc(undefined), "");
  // 0 and false are real values and must survive — `??` not `||` is the reason this works.
  assert.equal(esc(0), "0");
  assert.equal(esc(false), "false");
});

// ---------------------------------------------------------------- tsMs (previously untested)

test("tsMs parses the three timestamp shapes the API actually returns", () => {
  assert.equal(tsMs(1700000000000), 1700000000000, "epoch-ms passes through");
  assert.equal(tsMs(1700000000), 1700000000000, "epoch-seconds is scaled to ms");
  assert.equal(tsMs("2023-11-14T22:13:20.000Z"), 1700000000000, "ISO-8601 is parsed");
});

test("tsMs returns NaN for empty and unparseable input rather than 0", () => {
  // 0 would sort as 1970 and render as a real time; NaN is the honest answer and callers check it.
  for (const value of [null, undefined, "", "not a date"]) {
    assert.ok(Number.isNaN(tsMs(value)), `${String(value)} must be NaN`);
  }
});

test("tsMs does NOT stringify a numeric epoch through Date.parse", () => {
  // The documented bug this function exists for: Date.parse(1700000000000) is invalid.
  assert.ok(Number.isNaN(Date.parse(1700000000000)), "precondition: bare Date.parse fails on epoch-ms");
  assert.equal(tsMs(1700000000000), 1700000000000);
});

// ---------------------------------------------------------------- relTime (previously untested)

test("relTime formats minutes, hours and days from a past timestamp", () => {
  const now = Date.now();
  assert.equal(relTime(now - 5 * 60000), "5m");
  assert.match(relTime(now - 5 * 3600000), /^5h$/);
  assert.match(relTime(now - 5 * 86400000), /^5d$/);
});

test("relTime clamps a FUTURE timestamp to 0m instead of going negative", () => {
  assert.equal(relTime(Date.now() + 600000), "0m");
});

test("relTime returns empty for missing or unparseable input", () => {
  for (const value of [null, undefined, "", "nope"]) assert.equal(relTime(value), "");
});

// ---------------------------------------------------------------- fileSizeLabel (moved from app.js)

test("fileSizeLabel switches unit at each 1024 boundary", () => {
  assert.equal(fileSizeLabel(0), "0 B");
  assert.equal(fileSizeLabel(1023), "1023 B");
  assert.equal(fileSizeLabel(1024), "1.0 KB");
  assert.equal(fileSizeLabel(1024 * 1024 - 1), "1024.0 KB");
  assert.equal(fileSizeLabel(1024 * 1024), "1.0 MB");
});

test("fileSizeLabel maps missing sizes to 0 B", () => {
  // `bytes || 0` catches these three, so the guard works for the cases that actually occur.
  for (const value of [null, undefined, ""]) assert.equal(fileSizeLabel(value), "0 B");
});

test("PINNED: a non-numeric size renders 'NaN MB', not 0 B", () => {
  // FOUND HERE. `Number("abc")` is NaN; `NaN < 1024` and `NaN < 1024*1024` are both FALSE, so control
  // falls through to the MB branch and the label reads "NaN MB" — the widest unit, for an unparseable
  // input. `bytes || 0` only rescues falsy values, and "abc" is truthy.
  //
  // Pinned rather than fixed: v0.5.x is structural-only, and the API supplies file sizes as numbers so
  // this is latent. It is also exactly the kind of answer an assertion with an alternation would have
  // hidden — my first version of this test accepted either "0 B" or "NaN MB" and passed without telling
  // me which one happens. An assertion that accepts two answers has not measured anything.
  assert.equal(fileSizeLabel("abc"), "NaN MB");
  assert.equal(fileSizeLabel(-1), "-1 B", "a negative size takes the bytes branch unchanged");
});

// ---------------------------------------------------------------- usageFmtTokens (moved from app.js)

test("usageFmtTokens abbreviates at k, M and B", () => {
  assert.equal(usageFmtTokens(999), "999");
  assert.equal(usageFmtTokens(1000), "1.0k");
  assert.equal(usageFmtTokens(1_500_000), "1.5M");
  assert.equal(usageFmtTokens(2_000_000_000), "2.0B");
});

test("usageFmtTokens renders missing counts as 0, not as an empty cell", () => {
  // These land in a table of per-agent token usage; a blank cell reads as "no data" and 0 reads as
  // "measured zero", which are different operational answers.
  for (const value of [null, undefined, ""]) assert.equal(usageFmtTokens(value), "0");
});

// ---------------------------------------------------------------- usageResetLabel (moved from app.js)

test("usageResetLabel counts down in hours and minutes", () => {
  const future = new Date(Date.now() + (2 * 3600 + 30 * 60) * 1000).toISOString();
  assert.match(usageResetLabel(future), /^resets in 2h (29|30)m$/);
  const soon = new Date(Date.now() + 45 * 60000).toISOString();
  assert.match(usageResetLabel(soon), /^resets in (44|45)m$/);
});

test("usageResetLabel says 'resets soon' for a past or present reset time", () => {
  assert.equal(usageResetLabel(new Date(Date.now() - 60000).toISOString()), "resets soon");
});

test("usageResetLabel degrades to 'resets soon' rather than throwing on unparseable input", () => {
  // A quota badge that throws takes the whole render down with it. Note the answer is "resets soon" and
  // NOT the empty string the `catch` suggests: `new Date("nope") - new Date()` is NaN rather than a
  // throw, so `!(NaN > 0)` returns early and the catch is never reached. My first draft named this test
  // "returns empty"; running it showed the catch branch is unreachable for these inputs.
  for (const value of [null, undefined, "", "not-a-date"]) {
    assert.equal(usageResetLabel(value), "resets soon", `unexpected for ${String(value)}`);
  }
});
