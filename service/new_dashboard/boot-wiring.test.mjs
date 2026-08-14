// The boot wiring, tested by RUNNING it against a recording DOM.
//
// This is the phase that binds every control not reached through the delegated click dispatcher, and
// two of its details are the kind that break silently:
//
//   - the session env-group `toggle` listener must be registered in the CAPTURE phase, because
//     `toggle` does not bubble. In the bubble phase it simply never fires and collapse state stops
//     persisting, with nothing to see;
//   - `restorePersistedPreferences` reads localStorage, which THROWS in private mode rather than
//     returning null. One unavailable preference must not take the rest of the boot down with it.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import {
  restorePersistedPreferences,
  wireGlobalControls,
  wireInspectorGestures,
  wireSettingsControls,
} from "./boot-wiring.mjs";

/** A DOM that records every listener registration, keyed by element id. */
function recordingDom({ storage = {}, throwing = false } = {}) {
  const bound = [];
  const els = new Map();
  const make = (id) => ({
    id, innerHTML: "", textContent: "", className: "", value: "", hidden: false, checked: false,
    dataset: {}, style: {}, children: [], firstElementChild: null,
    classList: {
      added: [],
      add(c) { this.added.push(c); },
      remove() {}, toggle: () => false, contains: () => false,
    },
    querySelector: () => null, querySelectorAll: () => [],
    addEventListener: (type, fn, opts) => bound.push({ on: id, type, fn, opts }),
    removeEventListener() {}, appendChild() {}, setAttribute() {}, remove() {}, focus() {},
  });
  const saved = {
    document: globalThis.document,
    localStorage: globalThis.localStorage,
    fetch: globalThis.fetch,
    requestAnimationFrame: globalThis.requestAnimationFrame,
    window: globalThis.window,
    matchMedia: globalThis.matchMedia,
  };
  // `installRejectionToast` registers on `window`, which does not exist in Node.
  globalThis.window = {
    addEventListener: (type, fn) => bound.push({ on: "window", type, fn }),
    removeEventListener() {},
    matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }),
  };
  globalThis.matchMedia = globalThis.window.matchMedia;
  globalThis.requestAnimationFrame = (fn) => { fn(); return 1; };
  const store = new Map(Object.entries(storage));
  const reads = [];
  globalThis.localStorage = {
    getItem: (k) => {
      reads.push(k);
      if (throwing) throw new DOMException("private mode");
      return store.has(k) ? store.get(k) : null;
    },
    setItem: (k, v) => { if (throwing) throw new DOMException("private mode"); store.set(k, v); },
    removeItem: (k) => store.delete(k),
  };
  globalThis.document = {
    getElementById: (id) => { if (!els.has(id)) els.set(id, make(id)); return els.get(id); },
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => make("created"),
    addEventListener: (type, fn, opts) => bound.push({ on: "document", type, fn, opts }),
    removeEventListener() {},
    body: {
      children: [], firstElementChild: null, dataset: {}, appendChild() {},
      classList: { add() {}, remove() {}, toggle() {} }, style: { setProperty() {} },
    },
    documentElement: { dataset: {}, style: { setProperty() {} }, classList: { add() {}, remove() {}, toggle() {} }, setAttribute() {} },
    activeElement: null,
    visibilityState: "visible",
    title: "",
  };
  globalThis.fetch = async () => ({ ok: true, status: 200, statusText: "OK", json: async () => ({}), text: async () => "{}" });
  return { bound, els, reads, restore: () => Object.assign(globalThis, saved) };
}

const DEPS = {
  chatController: { render() {}, renderRail() {}, renderConversation() {}, close() {}, open() {} },
  closeInspector() {},
  refresh: async () => {},
  renderAll() {},
  renderSessionWorkspace() {},
  saveSettings: async () => {},
  chatCreateChannel: async () => {},
  inspect() {},
};

test("wiring binds listeners and never throws on a DOM missing every optional element", () => {
  // Most of these use `byId(...)?.addEventListener`, and the page they belong to may not be in the
  // document at all on a trimmed build. A throw here happens at BOOT, before anything is interactive.
  const h = recordingDom();
  try {
    assert.doesNotThrow(() => wireGlobalControls(DEPS));
    assert.ok(h.bound.length > 20, `expected the boot wiring to bind many listeners, got ${h.bound.length}`);
  } finally { h.restore(); }
});

test("THE env-group TOGGLE LISTENER IS REGISTERED IN THE CAPTURE PHASE", () => {
  // `toggle` does not bubble. Registered in the bubble phase it never fires, and the only symptom is
  // that session env-group collapse silently stops persisting — no error, no visible difference until
  // the next reload.
  const h = recordingDom();
  try {
    wireGlobalControls(DEPS);
    const toggles = h.bound.filter((b) => b.type === "toggle");
    assert.equal(toggles.length, 1, "exactly one toggle listener");
    assert.equal(toggles[0].opts, true, "…and it must be registered with capture=true");
  } finally { h.restore(); }
});

test("the touch listeners are PASSIVE, so the gesture cannot block scrolling", () => {
  const h = recordingDom();
  try {
    wireInspectorGestures();
    const touches = h.bound.filter((b) => b.type.startsWith("touch"));
    assert.equal(touches.length, 2, "touchstart and touchend");
    for (const t of touches) {
      assert.equal(t.opts?.passive, true, `${t.type} must be passive`);
    }
  } finally { h.restore(); }
});

test("the swipe closes the inspector only when it is a SHEET and the swipe went down far enough", () => {
  // The gesture exists for the mobile sheet layout. Firing it on the desktop drawer would close the
  // panel on any stray touch.
  const h = recordingDom();
  let closed = 0;
  try {
    wireInspectorGestures();
    const start = h.bound.find((b) => b.type === "touchstart").fn;
    const end = h.bound.find((b) => b.type === "touchend").fn;
    const inspector = h.els.get("inspector");
    inspector.classList.contains = () => false;   // not a sheet
    start({ touches: [{ clientY: 0 }] });
    end({ changedTouches: [{ clientY: 500 }] });
    assert.equal(closed, 0, "a non-sheet inspector must ignore the swipe");
  } finally { h.restore(); }
});

test("A THROWING localStorage NO LONGER BREAKS THE BOOT", () => {
  // localStorage THROWS in private mode rather than returning null, and this test found the one
  // reader in the whole boot path without a guard: `preferredNavCollapsed` in layout-prefs.mjs called
  // `getItem` bare. It runs near the END of the restore — after `setPage('chat')` and
  // `updateStaticLinks()` — so a private window painted the page and then stopped, with the Work-view
  // restore and everything after it silently never running.
  //
  // It was pinned as a throw for one commit, because the finding surfaced inside a relocation whose
  // bodies were proved byte-identical and a guard there would have smuggled a behaviour change into a
  // refactor. This is the assertion flipped, which is what made the fix visible rather than silent.
  const h = recordingDom({ throwing: true });
  try {
    assert.doesNotThrow(() => restorePersistedPreferences({ setPage() {} }));
    assert.ok(h.reads.includes("aify.next.navCollapsed"),
      "…and it still ATTEMPTS the read — the guard must not have been achieved by deleting it");
  } finally { h.restore(); }
});

test("THE WHOLE restore completes on a throwing storage, not just its first half", () => {
  // The specific regression this replaced: the boot used to get as far as the landing page and stop.
  // Reaching the LAST statement is what says the guard is in the right place, so the observable is the
  // final one — the Work-view restore, which runs after the nav preference.
  const h = recordingDom({ throwing: true });
  const pages = [];
  try {
    restorePersistedPreferences({ setPage: (p) => pages.push(p) });
    assert.deepEqual(pages, ["chat"], "the landing page is set");
    assert.ok(h.els.get("attention-strip").classList.added.includes("collapsed"),
      "an unreadable attention preference falls back to collapsed, which its catch does explicitly");
    assert.ok(h.reads.includes("aifyWorkView"),
      "the LAST read in the block must be reached — that is the half that used to be skipped");
  } finally { h.restore(); }
});

test("an EXPLICIT '0' keeps the attention strip open", () => {
  // The other direction: the fallback must not override a real choice.
  const h = recordingDom({ storage: { "aify.next.attentionCollapsed": "0" } });
  try {
    restorePersistedPreferences({ setPage() {} });
    // The strip is not merely left un-collapsed — it is never REACHED, because the branch that adds the
    // class is the only thing that looks it up. Asserting the class list would have quietly created the
    // element and passed for the wrong reason.
    const added = h.els.get("attention-strip")?.classList.added ?? [];
    assert.ok(!added.includes("collapsed"), "an explicit choice to keep it open must be honoured");
  } finally { h.restore(); }
});

test("restore lands on the CHAT page", () => {
  // Chat-first landing. Restoring the last page instead would drop an operator into whatever screen
  // they happened to close on, which for the Console is a pane with nothing mounted.
  const h = recordingDom();
  const pages = [];
  try {
    restorePersistedPreferences({ setPage: (p) => pages.push(p) });
    assert.deepEqual(pages, ["chat"]);
  } finally { h.restore(); }
});

test("chat rail preferences are restored from storage", () => {
  const h = recordingDom({
    storage: { "aify.next.chatPrefs": JSON.stringify({ liveOnly: true, peek: true, sortMode: "unread", statusFilter: ["working"] }) },
  });
  try {
    restorePersistedPreferences({ setPage() {} });
    assert.equal(state.chat.liveOnly, true);
    assert.equal(state.chat.peek, true);
    assert.equal(state.chat.sortMode, "unread");
    assert.ok(state.chat.statusFilter instanceof Set, "the status filter must be rehydrated as a Set");
    assert.ok(state.chat.statusFilter.has("working"));
  } finally { h.restore(); }
});

test("CORRUPT stored chat preferences are ignored rather than crashing the boot", () => {
  // It is JSON from a previous version of the app; the shape is not guaranteed.
  for (const stored of ["not json", "null", "[]", '{"statusFilter":"not-an-array"}']) {
    const h = recordingDom({ storage: { "aify.next.chatPrefs": stored } });
    try {
      assert.doesNotThrow(() => restorePersistedPreferences({ setPage() {} }), stored);
    } finally { h.restore(); }
  }
});

test("the settings controls bind, and the live preview is separate from save", () => {
  // Appearance preview repaints from UNSAVED form values; Reset is what puts the saved ones back. They
  // are different listeners on purpose, and collapsing them would make every preview a save.
  const h = recordingDom();
  try {
    wireSettingsControls({ saveSettings: async () => {} });
    const ids = h.bound.map((b) => `${b.on}:${b.type}`);
    assert.ok(ids.includes("settings-save:click"));
    assert.ok(ids.includes("settings-reset:click"));
    assert.ok(ids.includes("settings-form:input"), "live preview on input");
    assert.ok(ids.includes("settings-form:change"), "…and on change, for selects and colour pickers");
  } finally { h.restore(); }
});

test("a failing save REPORTS rather than becoming an unhandled rejection", async () => {
  const h = recordingDom();
  try {
    wireSettingsControls({ saveSettings: async () => { throw new Error("nope"); } });
    const onSave = h.bound.find((b) => b.on === "settings-save" && b.type === "click").fn;
    assert.doesNotThrow(() => onSave());
    await new Promise((r) => setTimeout(r, 0));
  } finally { h.restore(); }
});
