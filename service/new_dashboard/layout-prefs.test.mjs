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

// --- storage that is unavailable rather than empty ----------------------------------------------
//
// localStorage THROWS in a private window and wherever site data is blocked by policy; it does not
// return null. `toggleSessionGroupCollapsed` has always guarded for that. The nav pair did not, and
// `boot-wiring.test.mjs` caught it by running the whole boot against a throwing storage — the page
// painted and then the boot stopped, because `preferredNavCollapsed` is called near the end of it.
//
// Both directions are asserted for each: the guard must not have been achieved by dropping the read.

function withStorage({ throwing = false, stored = {}, narrow = false } = {}) {
  const saved = {
    localStorage: globalThis.localStorage,
    window: globalThis.window,
    document: globalThis.document,
  };
  const reads = [];
  const writes = [];
  const store = new Map(Object.entries(stored));
  globalThis.localStorage = {
    getItem: (k) => { reads.push(k); if (throwing) throw new DOMException("private mode"); return store.has(k) ? store.get(k) : null; },
    setItem: (k, v) => { writes.push([k, v]); if (throwing) throw new DOMException("private mode"); store.set(k, v); },
    removeItem: (k) => store.delete(k),
  };
  globalThis.window = { matchMedia: () => ({ matches: narrow }) };
  const toggled = [];
  const el = {
    classList: { toggle: (c, on) => toggled.push([c, on]) },
    setAttribute() {},
  };
  globalThis.document = { getElementById: () => el, querySelector: () => null, querySelectorAll: () => [] };
  return { reads, writes, toggled, restore: () => Object.assign(globalThis, saved) };
}

test("preferredNavCollapsed FALLS BACK TO THE VIEWPORT when storage throws", () => {
  for (const narrow of [true, false]) {
    const h = withStorage({ throwing: true, narrow });
    try {
      assert.equal(preferredNavCollapsed(), narrow,
        "an unreadable preference must answer the same way as no preference at all");
      assert.ok(h.reads.includes("aify.next.navCollapsed"),
        "…and it must still have TRIED, or the guard was achieved by deleting the read");
    } finally { h.restore(); }
  }
});

test("a STORED preference still wins over the viewport", () => {
  // The other direction. A guard written as an unconditional fallback would pass the test above and
  // silently ignore every operator who has ever clicked the toggle.
  const h = withStorage({ stored: { "aify.next.navCollapsed": "1" }, narrow: false });
  try {
    assert.equal(preferredNavCollapsed(), true, "an explicit '1' must collapse on a wide viewport");
  } finally { h.restore(); }

  const open = withStorage({ stored: { "aify.next.navCollapsed": "0" }, narrow: true });
  try {
    assert.equal(preferredNavCollapsed(), false, "an explicit '0' must stay open on a narrow one");
  } finally { open.restore(); }
});

test("setNavCollapsed STILL COLLAPSES THE SIDEBAR when the write throws", () => {
  // The DOM update is deliberately outside the try. A guard that wrapped the whole function would make
  // the sidebar toggle do nothing at all in a private window — a visible, immediate break, traded for
  // the invisible one.
  const h = withStorage({ throwing: true });
  try {
    assert.doesNotThrow(() => setNavCollapsed(true));
    assert.deepEqual(h.toggled, [["nav-collapsed", true]], "the class must still have been applied");
    assert.deepEqual(h.writes, [["aify.next.navCollapsed", "1"]], "…and the persist must still be attempted");
  } finally { h.restore(); }
});

test("setNavCollapsed persists normally when storage works", () => {
  const h = withStorage();
  try {
    setNavCollapsed(false);
    assert.deepEqual(h.writes, [["aify.next.navCollapsed", "0"]]);
  } finally { h.restore(); }
});
