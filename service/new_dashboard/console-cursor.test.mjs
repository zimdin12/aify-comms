// Where a console's cursor is, when the answer is a value and when there is no answer at all.
//
// ONLY `cursorFromSnapshot` IS TESTED HERE, and that is deliberate rather than an omission.
// `holdFrame` and `drainHeldFrames` are driven through the real socket and the real recovery in
// `the-console-does-not-stall-while-it-resyncs.test.mjs` (fourteen cases, including the cap, the
// unknown-position seed, the non-adjacent stop and the bounded retry) and through the real mount in
// `a-mount-keeps-the-frames-that-arrive-while-it-fetches.test.mjs`. A second test of the same
// property is cost without coverage, and a unit test of a drain is a weaker instrument than one
// that drives the consumer.
//
// WHAT IS NOT COVERED ANYWHERE ELSE is the difference between a field that is NULL and a field that
// is ABSENT. That distinction is the entire finding: `outputSeq ?? seq ?? current` collapses them,
// and it did so in two readers -- the recovery kept a known cursor of 4 while the server had just
// said it does not know where the screen is, and the mount kept whatever a live frame had set
// moments before it wiped the screen. Both bugs are one operator away from each other, so the read
// is pinned as a value here rather than only through its two consumers.

import assert from "node:assert/strict";
import test from "node:test";

import { cursorFromSnapshot } from "./console-cursor.mjs";

test("AN EXPLICIT NULL IS UNKNOWN, and unknown is -1", () => {
  assert.equal(cursorFromSnapshot({ outputSeq: null }, 4), -1);
  assert.equal(cursorFromSnapshot({ seq: null }, 4), -1);
});

test("AN ABSENT FIELD IS NOT UNKNOWN, so the caller's cursor stands", () => {
  // The distinction the `??` chain could not make. A response that never mentions a sequence says
  // NOTHING about where the screen is; one that says null says the server does not know.
  assert.equal(cursorFromSnapshot({}, 4), 4);
  assert.equal(cursorFromSnapshot(undefined, 4), 4);
  assert.equal(cursorFromSnapshot(null, 4), 4);
  assert.equal(cursorFromSnapshot({ snapshot: "hello" }, 7), 7);
});

test("A NUMBER IS THE NUMBER, including zero", () => {
  // POSITIVE CONTROL, and the zero is the half that matters: `outputSeq: 0` is a real position, and
  // any read that treats a falsy value as absence hands back the caller's cursor instead.
  assert.equal(cursorFromSnapshot({ outputSeq: 12 }, 4), 12);
  assert.equal(cursorFromSnapshot({ outputSeq: 0 }, 4), 0);
  assert.equal(cursorFromSnapshot({ outputSeq: 3 }, 9), 3, "a snapshot BEHIND the cursor still wins");
});

test("`outputSeq` OWNS THE ANSWER, and `seq` is only consulted when it is absent", () => {
  // The GET returns `outputSeq`; only a WS frame carries `seq`. A response carrying both, with a
  // null `outputSeq`, is the server saying unknown -- reading `seq` there would launder it back
  // into a known position, which is the tear this whole branch exists to close.
  assert.equal(cursorFromSnapshot({ outputSeq: 5, seq: 11 }, 4), 5);
  assert.equal(cursorFromSnapshot({ outputSeq: null, seq: 11 }, 4), -1);
  assert.equal(cursorFromSnapshot({ seq: 11 }, 4), 11);
});
