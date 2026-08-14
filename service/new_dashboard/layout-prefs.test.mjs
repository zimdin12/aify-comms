// Layout preferences, tested by CALLING them.
//
// All three lived in app.js and were unreachable. They fail the same quiet way: the click works, the
// layout changes, and the choice is gone on the next reload — nothing on screen distinguishes that from
// working, so it is only ever noticed as "the dashboard keeps forgetting".

import assert from "node:assert/strict";
import test from "node:test";

import { preferredNavCollapsed, setNavCollapsed, toggleSessionGroupCollapsed } from "./layout-prefs.mjs";

/** Storage + DOM stubs. `refuse` makes every write throw, as private mode does. */
function withPrefs({ stored = {}, refuse = false, matchMedia = false, missing = false } = {}, run) {
  const hadLs = "localStorage" in globalThis;
  const hadWin = "window" in globalThis;
  const hadDoc = "document" in globalThis;
  const prevLs = globalThis.localStorage;
  const prevWin = globalThis.window;
  const prevDoc = globalThis.document;
  const store = new Map(Object.entries(stored));
  const els = {
    "app-shell": { classes: new Map(), classList: { toggle(c, on) { els["app-shell"].classes.set(c, on); } } },
    "toggle-nav": { attrs: {}, setAttribute(k, v) { els["toggle-nav"].attrs[k] = v; } },
  };
  globalThis.localStorage = {
    setItem: (k, v) => { if (refuse) throw new Error("private mode"); store.set(k, v); },
    getItem: (k) => (store.has(k) ? store.get(k) : null),
  };
  globalThis.window = { matchMedia: () => ({ matches: matchMedia }) };
  globalThis.document = { getElementById: (id) => (missing ? null : els[id] || null) };
  try {
    return run({ store, els });
  } finally {
    if (hadLs) globalThis.localStorage = prevLs; else delete globalThis.localStorage;
    if (hadWin) globalThis.window = prevWin; else delete globalThis.window;
    if (hadDoc) globalThis.document = prevDoc; else delete globalThis.document;
  }
}

// --- setNavCollapsed ---------------------------------------------------------------------------

test("setNavCollapsed drives the class, BOTH aria attributes, and storage together", () => {
  // Four effects in five lines, and `aria-pressed` is the one a sighted reviewer never notices missing:
  // the sidebar toggle is a button whose state is conveyed by layout alone, so without it a screen
  // reader announces the same thing whether the nav is open or shut.
  withPrefs({}, (h) => {
    setNavCollapsed(true);
    assert.equal(h.els["app-shell"].classes.get("nav-collapsed"), true);
    assert.equal(h.els["toggle-nav"].attrs["aria-pressed"], "true");
    assert.equal(h.els["toggle-nav"].attrs.title, "Expand sidebar");
    assert.equal(h.store.get("aify.next.navCollapsed"), "1");

    setNavCollapsed(false);
    assert.equal(h.els["app-shell"].classes.get("nav-collapsed"), false);
    assert.equal(h.els["toggle-nav"].attrs["aria-pressed"], "false");
    assert.equal(h.els["toggle-nav"].attrs.title, "Collapse sidebar");
    assert.equal(h.store.get("aify.next.navCollapsed"), "0");
  });
});

test("THE TITLE NAMES THE ACTION, not the state — collapsed offers 'Expand'", () => {
  // Easy to get backwards, and backwards it actively misleads: a tooltip reading "Collapse sidebar" on
  // an already-collapsed nav tells the operator the button does nothing.
  withPrefs({}, (h) => {
    setNavCollapsed(true);
    assert.equal(h.els["toggle-nav"].attrs.title, "Expand sidebar");
  });
});

test("any truthy value collapses — the class takes a real boolean", () => {
  // `Boolean(collapsed)`. `classList.toggle(c, "yes")` and `toggle(c, 0)` behave differently across
  // engines when the second argument is not a boolean; the coercion is what makes this predictable.
  for (const value of [1, "yes", {}]) {
    withPrefs({}, (h) => {
      setNavCollapsed(value);
      assert.equal(h.els["app-shell"].classes.get("nav-collapsed"), true, JSON.stringify(value));
    });
  }
});

test("a missing shell or toggle button does not throw", () => {
  // `shell?.` and `byId('toggle-nav')?.` — this runs during boot, before the shell may be in the DOM.
  withPrefs({ missing: true }, (h) => {
    assert.doesNotThrow(() => setNavCollapsed(true));
    assert.equal(h.store.get("aify.next.navCollapsed"), "1", "the choice is still persisted");
  });
});

// --- preferredNavCollapsed ---------------------------------------------------------------------

test("a STORED choice wins over the viewport", () => {
  // The operator's explicit choice must survive a window resize. Consulting matchMedia first would
  // re-collapse the nav every time a narrow window was opened, undoing a deliberate setting.
  withPrefs({ stored: { "aify.next.navCollapsed": "0" }, matchMedia: true }, () => {
    assert.equal(preferredNavCollapsed(), false, "stored '0' beats a narrow viewport");
  });
  withPrefs({ stored: { "aify.next.navCollapsed": "1" }, matchMedia: false }, () => {
    assert.equal(preferredNavCollapsed(), true);
  });
});

test("with NOTHING stored it falls back to the viewport width", () => {
  // First visit. A narrow screen starts collapsed; a wide one does not.
  withPrefs({ matchMedia: true }, () => assert.equal(preferredNavCollapsed(), true));
  withPrefs({ matchMedia: false }, () => assert.equal(preferredNavCollapsed(), false));
});

test("`if (stored)` means an empty string ALSO falls through to the viewport", () => {
  // Worth pinning: the guard is truthiness, not `!== null`. A cleared key behaves like a first visit,
  // which is the sensible reading — but it is a behaviour, not an accident.
  withPrefs({ stored: { "aify.next.navCollapsed": "" }, matchMedia: true }, () => {
    assert.equal(preferredNavCollapsed(), true);
  });
});

// --- toggleSessionGroupCollapsed ----------------------------------------------------------------

const GROUPS = "aifyCollapsedSessionGroups";

test("collapsing adds an environment and expanding removes it, as a JSON array", () => {
  // A Set does not survive JSON.stringify — it serialises as `{}` — so the spread is what makes the
  // collapsed groups restorable at all, and the failure is silent: the write succeeds, the read finds
  // nothing, and every group is expanded again on reload.
  withPrefs({}, (h) => {
    toggleSessionGroupCollapsed("env-1", true);
    assert.deepEqual(JSON.parse(h.store.get(GROUPS)), ["env-1"]);

    toggleSessionGroupCollapsed("env-2", true);
    assert.deepEqual(JSON.parse(h.store.get(GROUPS)).sort(), ["env-1", "env-2"]);

    toggleSessionGroupCollapsed("env-1", false);
    assert.deepEqual(JSON.parse(h.store.get(GROUPS)), ["env-2"]);
  });
});

test("collapsing the same environment twice does not duplicate it", () => {
  withPrefs({}, (h) => {
    toggleSessionGroupCollapsed("env-1", true);
    toggleSessionGroupCollapsed("env-1", true);
    assert.deepEqual(JSON.parse(h.store.get(GROUPS)), ["env-1"]);
  });
});

test("CORRUPT OR MISSING stored JSON is survived, not propagated", () => {
  // `JSON.parse(... || '[]') || []` inside a try. A half-written value would otherwise throw on every
  // group toggle for the rest of that browser profile's life — and the operator's only clue would be
  // that collapsing groups stopped working.
  for (const bad of ["not json", "null", "{}", '"a string"']) {
    withPrefs({ stored: { [GROUPS]: bad } }, () => {
      assert.doesNotThrow(() => toggleSessionGroupCollapsed("env-1", true), bad);
    });
  }
});

test("a refusing storage is swallowed", () => {
  withPrefs({ refuse: true }, () => {
    assert.doesNotThrow(() => toggleSessionGroupCollapsed("env-1", true));
  });
});
