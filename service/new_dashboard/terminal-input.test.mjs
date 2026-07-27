import assert from 'node:assert/strict';
import { test } from 'node:test';

import * as terminalInput from './terminal-input.mjs';

const { wheelInputSequence, WHEEL_MAX_LINES } = terminalInput;

test('waitForTerminalSize waits until the bridge-applied dimensions are visible', async () => {
  assert.equal(typeof terminalInput.waitForTerminalSize, 'function');
  const sizes = [{ cols: 154, rows: 24 }, { cols: 153, rows: 24 }];
  let reads = 0;

  await terminalInput.waitForTerminalSize({
    cols: 153,
    rows: 24,
    readSize: async () => sizes[Math.min(reads++, sizes.length - 1)],
    delay: async () => {},
  });

  assert.equal(reads, 2);
});

test('forceTerminalRepaint changes width before restoring it', async () => {
  assert.equal(typeof terminalInput.forceTerminalRepaint, 'function');
  const calls = [];

  await terminalInput.forceTerminalRepaint({
    cols: 154,
    rows: 24,
    resize: async (cols, rows) => calls.push([cols, rows]),
    waitForSize: async (cols, rows) => calls.push(['wait', cols, rows]),
  });

  assert.deepEqual(calls, [
    [153, 24], ['wait', 153, 24],
    [154, 24], ['wait', 154, 24],
  ]);
});

test('forceTerminalRepaint still changes width at the minimum column count', async () => {
  const calls = [];

  await terminalInput.forceTerminalRepaint({
    cols: 20,
    rows: 5,
    resize: async (cols, rows) => calls.push([cols, rows]),
    waitForSize: async (cols, rows) => calls.push(['wait', cols, rows]),
  });

  assert.deepEqual(calls, [
    [21, 5], ['wait', 21, 5],
    [20, 5], ['wait', 20, 5],
  ]);
});

// ── wheel → arrow translation (operator report 2026-07-27) ────────────────────────────────────
//
// `wheel` does not require focus, so scrolling the page with the pointer merely HOVERING over a
// console injected up to 5 synthetic arrow keypresses per event into that agent's live PTY. Inside
// a composer, arrows move the cursor — so an operator scrolling to read scattered their own
// subsequent typing across the draft. That was the reported "I try to write and delete stuff but I
// can't". The focus gate is the corruption guard.

test("wheelInputSequence: does NOT inject when the terminal is unfocused (the corruption guard)", () => {
  assert.equal(
    wheelInputSequence({ bufferType: "alternate", canInput: true, focused: false, deltaY: 120 }),
    null,
    "hover-scroll is navigation, not input — it must never reach the PTY",
  );
});

test("wheelInputSequence: injects down-arrows when focused in the alternate screen", () => {
  const seq = wheelInputSequence({ bufferType: "alternate", canInput: true, focused: true, deltaY: 120 });
  assert.equal(seq, "[B".repeat(3));
});

test("wheelInputSequence: negative delta scrolls UP", () => {
  const seq = wheelInputSequence({ bufferType: "alternate", canInput: true, focused: true, deltaY: -40 });
  assert.equal(seq, "[A");
});

test("wheelInputSequence: normal buffer is left to xterm's native scrollback", () => {
  assert.equal(wheelInputSequence({ bufferType: "normal", canInput: true, focused: true, deltaY: 120 }), null);
  assert.equal(wheelInputSequence({ bufferType: undefined, canInput: true, focused: true, deltaY: 120 }), null);
});

test("wheelInputSequence: a console not accepting input is never written to", () => {
  assert.equal(wheelInputSequence({ bufferType: "alternate", canInput: false, focused: true, deltaY: 120 }), null);
});

test("wheelInputSequence: degenerate deltas emit nothing", () => {
  for (const deltaY of [0, -0, NaN, undefined, null, "", "abc", Infinity, -Infinity]) {
    assert.equal(
      wheelInputSequence({ bufferType: "alternate", canInput: true, focused: true, deltaY }),
      null,
      `deltaY=${String(deltaY)} is not a scroll and must not synthesise a keystroke`,
    );
  }
});

test("wheelInputSequence: burst is capped so one gesture cannot flood the PTY", () => {
  const seq = wheelInputSequence({ bufferType: "alternate", canInput: true, focused: true, deltaY: 100000 });
  assert.equal(seq.length, "[B".length * WHEEL_MAX_LINES);
});

test("wheelInputSequence: a tiny delta still moves exactly one line", () => {
  assert.equal(wheelInputSequence({ bufferType: "alternate", canInput: true, focused: true, deltaY: 1 }), "[B");
});
