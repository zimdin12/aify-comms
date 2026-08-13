// Matching a remote terminal's width without losing the ability to come back.
//
// The console mirrors a terminal running elsewhere. When that remote pane is wider than what fits locally,
// the xterm is widened and the container marked so CSS can scroll it. The failure this guards is not a
// cosmetic one: comparing against the terminal's CURRENT width instead of its FITTED width means a pane
// that once went wide can never shrink, because it keeps measuring itself against its own widened state.

import assert from "node:assert/strict";
import { test } from "node:test";

import { applyRenderedWidth } from "./terminal-width.mjs";

// The smallest fakes that record what was asked of them.
function fakes({ cols = 80, rows = 24, resizeThrows = false } = {}) {
  const term = {
    cols, rows, resizes: [],
    resize(c, r) {
      this.resizes.push([c, r]);
      if (resizeThrows) throw new Error("xterm is tearing down");
      this.cols = c; this.rows = r;
    },
  };
  const classes = new Set();
  const container = { classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c) }, classes };
  return { term, container, classes };
}

const WIDE = "console-wide-mirror";

test("A WIDER REMOTE PANE widens the terminal and marks the container", () => {
  const { term, container, classes } = fakes({ cols: 80 });
  const entry = { fitCols: 80 };
  applyRenderedWidth(entry, term, container, { terminal: { renderedCols: 200, renderedRows: 50 } });
  assert.deepEqual(term.resizes.at(-1), [200, 50], "it must resize to the remote's rendered size");
  assert.ok(classes.has(WIDE), "…and mark the container so CSS can scroll it");
  assert.equal(entry.widened, true);
  assert.equal(entry.renderedCols, 200);
});

test("IT SHRINKS BACK, which is the whole reason it compares against fitCols", () => {
  // The terminal is ALREADY widened from a previous snapshot — `term.cols` is 200 — and the remote is now
  // narrow again. Comparing against `term.cols` would find 80 < 200 and leave it wide forever.
  const { term, container, classes } = fakes({ cols: 200 });
  classes.add(WIDE);
  const entry = { fitCols: 80, widened: true, renderedCols: 200 };
  applyRenderedWidth(entry, term, container, { terminal: { renderedCols: 60 } });
  assert.deepEqual(term.resizes.at(-1), [80, term.rows], "it must return to the FITTED width");
  assert.ok(!classes.has(WIDE), "…and drop the wide marker");
  assert.equal(entry.widened, false);
  assert.equal(entry.renderedCols, 80);
});

test("a remote no WIDER than the fit does not widen", () => {
  // Equal is not wider. Widening on equality would toggle the marker on every snapshot.
  const { term, container, classes } = fakes({ cols: 80 });
  applyRenderedWidth({ fitCols: 80 }, term, container, { terminal: { renderedCols: 80 } });
  assert.ok(!classes.has(WIDE));
});

test("OWNING THE PTY IGNORES THE REMOTE WIDTH ENTIRELY", () => {
  // If this browser owns the pty it IS the authority; the remote's rendered width is a stale echo of our
  // own. It resets to fitted and returns without consulting the snapshot at all.
  const { term, container, classes } = fakes({ cols: 200 });
  classes.add(WIDE);
  const entry = { fitCols: 80, widened: true };
  applyRenderedWidth(entry, term, container, { terminal: { renderedCols: 400 } }, true);
  assert.deepEqual(term.resizes.at(-1), [80, term.rows], "owning the pty resets to the fitted width");
  assert.ok(!classes.has(WIDE), "…and never marks it wide, however wide the remote claims to be");
  assert.equal(entry.widened, false);
});

test("A RESIZE THAT THROWS DOES NOT ABORT THE CALLER", () => {
  // xterm throws on resize during teardown or before the renderer attaches. This runs inside the
  // snapshot-apply loop, so an escaping throw freezes the console mid-update.
  const { term, container } = fakes({ cols: 80, resizeThrows: true });
  assert.doesNotThrow(() => applyRenderedWidth({ fitCols: 80 }, term, container,
    { terminal: { renderedCols: 200 } }));
  assert.doesNotThrow(() => applyRenderedWidth({ fitCols: 80 }, term, container,
    { terminal: { renderedCols: 200 } }, true), "…on the owns-pty path too");
});

test("it works with no entry and no container", () => {
  // Both are optional at the call sites — a console can be mid-mount. Neither may throw.
  const { term } = fakes({ cols: 80 });
  assert.doesNotThrow(() => applyRenderedWidth(null, term, null, { terminal: { renderedCols: 200 } }));
  assert.deepEqual(term.resizes.at(-1), [200, term.rows], "…and it still resizes the terminal it was given");
});

test("a junk or absent snapshot falls back to the terminal's own width", () => {
  // `renderedCols` arrives from the service and may be missing, zero or unparseable. None of those mean
  // "widen to nothing" — they mean "no remote opinion", which is the fitted width.
  for (const data of [undefined, null, {}, { terminal: {} }, { terminal: { renderedCols: "junk" } },
    { terminal: { renderedCols: 0 } }]) {
    const { term, container, classes } = fakes({ cols: 200 });
    const entry = { fitCols: 80 };
    applyRenderedWidth(entry, term, container, data);
    assert.ok(!classes.has(WIDE), `${JSON.stringify(data)} must not widen`);
    assert.equal(entry.renderedCols, 80, "…and must record the fitted width");
  }
});

test("with no fitCols recorded it falls back to the terminal's current width", () => {
  // `entry.fitCols` is set when the pane is fitted; before that the terminal's own width is the best
  // available answer, and using 0 would resize the terminal to nothing.
  const { term } = fakes({ cols: 120 });
  const entry = {};
  applyRenderedWidth(entry, term, null, { terminal: { renderedCols: 100 } });
  assert.equal(entry.renderedCols, 120, "the current width stands in for a missing fit");
});
