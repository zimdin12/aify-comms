// Real tests for the chat rail's preferences.
//
// The pairing is the point: `persistChatPrefs` writes nine fields and `syncChatChips` reads the same nine
// back onto the chips. A preference added to one and not the other is half-implemented in a way that looks
// fine — it either survives a reload with a chip showing the wrong state, or the chip works and the setting
// is forgotten. Neither had a test while this lived in app.js.
//
// SEALING. `state` is a shared singleton; `localStorage` and `document` do not exist in Node. Each is
// installed per test and removed afterwards, so nothing here can pass because the host provided it.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import {
  persistChatDrafts,
  persistChatPrefs,
  syncChatChips,
  toggleChatCompact,
  toggleChatPeek,
} from "./chat-prefs.mjs";

function withStorage(run) {
  const had = "localStorage" in globalThis;
  const store = new Map();
  globalThis.localStorage = {
    setItem: (k, v) => store.set(k, v),
    getItem: (k) => (store.has(k) ? store.get(k) : null),
  };
  try {
    return run(store);
  } finally {
    if (!had) delete globalThis.localStorage;
  }
}

const PERSISTED = [
  "liveOnly", "openOnly", "workingUp", "unreadOnly",
  "scope", "statusFilter", "sortMode", "compact", "peek",
];

test("every chat preference survives a round trip to storage", () => {
  state.chat = {
    liveOnly: true, openOnly: false, workingUp: true, unreadOnly: false,
    scope: "dms", statusFilter: new Set(["working", "blocked"]),
    sortMode: "recent", compact: true, peek: false,
  };
  const saved = withStorage((store) => {
    persistChatPrefs();
    return JSON.parse(store.get("aify.next.chatPrefs"));
  });

  for (const key of PERSISTED) {
    assert.ok(key in saved, `"${key}" is missing from the persisted set — it will not survive a reload`);
  }
  assert.equal(saved.scope, "dms");
  assert.equal(saved.compact, true);
  assert.deepEqual(saved.statusFilter, ["working", "blocked"],
    "the status filter is a Set and must be serialised as an array, not as {}");
});

test("an absent status filter persists as an empty array rather than null", () => {
  // `[...(x || [])]`. Persisting null would make the reload path branch on a shape it does not expect.
  state.chat = { scope: "all" };
  const saved = withStorage((store) => {
    persistChatPrefs();
    return JSON.parse(store.get("aify.next.chatPrefs"));
  });
  assert.deepEqual(saved.statusFilter, []);
});

test("persisting never throws when storage refuses", () => {
  // Private browsing and quota errors both throw from setItem. A preference toggle that raises would break
  // the click that set it.
  const had = "localStorage" in globalThis;
  globalThis.localStorage = { setItem: () => { throw new Error("QuotaExceeded"); } };
  try {
    state.chat = { scope: "all" };
    persistChatPrefs();
  } finally {
    if (!had) delete globalThis.localStorage;
  }
});

function chip(attrs = {}) {
  const classes = new Set();
  const el = {
    dataset: attrs,
    attrs: {},
    classList: {
      toggle: (c, on) => { if (on) classes.add(c); else classes.delete(c); return on; },
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c),
    },
    setAttribute(k, v) { this.attrs[k] = v; },
  };
  return el;
}

function withChips(map, run) {
  const had = "document" in globalThis;
  globalThis.document = {
    querySelectorAll: (sel) => map[sel] || [],
    querySelector: (sel) => (map[sel] || [])[0] || null,
  };
  try {
    return run();
  } finally {
    if (!had) delete globalThis.document;
  }
}

test("a chip's pressed state is mirrored into aria-pressed, not just its class", () => {
  // THE ACCESSIBILITY CONTRACT. The status dots carry no text, so without aria-pressed their toggle state
  // is conveyed by colour alone.
  const on = chip({ chatToggle: "liveOnly" });
  const off = chip({ chatToggle: "unreadOnly" });
  state.chat = { liveOnly: true, unreadOnly: false, scope: "all" };

  withChips({ "[data-chat-toggle]": [on, off] }, syncChatChips);

  assert.equal(on.classList.contains("active"), true);
  assert.equal(on.attrs["aria-pressed"], "true");
  assert.equal(off.classList.contains("active"), false);
  assert.equal(off.attrs["aria-pressed"], "false", "the OFF state must be stated, not merely absent");
});

test("the scope chips press exactly the active scope, defaulting to 'all'", () => {
  const all = chip({ chatScope: "all" });
  const dms = chip({ chatScope: "dms" });

  state.chat = { scope: "dms" };
  withChips({ "[data-chat-scope]": [all, dms] }, syncChatChips);
  assert.equal(dms.attrs["aria-pressed"], "true");
  assert.equal(all.attrs["aria-pressed"], "false");

  state.chat = {};
  withChips({ "[data-chat-scope]": [all, dms] }, syncChatChips);
  assert.equal(all.attrs["aria-pressed"], "true", "no scope set must fall back to 'all'");
});

test("status chips reflect the filter Set, and a non-Set filter presses nothing", () => {
  const working = chip({ chatStatus: "working" });
  const offline = chip({ chatStatus: "offline" });

  state.chat = { scope: "all", statusFilter: new Set(["working"]) };
  withChips({ "[data-chat-status]": [working, offline] }, syncChatChips);
  assert.equal(working.attrs["aria-pressed"], "true");
  assert.equal(offline.attrs["aria-pressed"], "false");

  // The field has been an array at times. Guarding on `instanceof Set` means a wrong shape presses
  // nothing rather than throwing mid-render.
  state.chat = { scope: "all", statusFilter: ["working"] };
  withChips({ "[data-chat-status]": [working, offline] }, syncChatChips);
  assert.equal(working.attrs["aria-pressed"], "false");
});

test("syncing survives a page with none of the chips present", () => {
  // It runs from the shared render path, not only on the chat page.
  state.chat = { scope: "all" };
  withChips({}, syncChatChips);
});

// --- drafts ---------------------------------------------------------------
//
// Joined this module in v0.5.4: the same subject as the preferences above — per-conversation state the
// rail restores on reload. A half-written message and its "draft" badge are supposed to survive a
// refresh, and nothing tested that they do.

test("only NON-EMPTY drafts are persisted, so the badge cannot outlive the text", () => {
  // The rail shows a "draft" marker for any key present in storage. Persisting an emptied box would
  // leave that badge on a conversation with nothing in it, which is worse than losing the draft.
  state.chat = {
    drafts: { a1: "half a message", a2: "", a3: "   ", a4: "\t\n", a5: "real" },
  };
  const saved = withStorage((store) => {
    persistChatDrafts();
    return JSON.parse(store.get("aifyChatDrafts"));
  });
  assert.deepEqual(Object.keys(saved).sort(), ["a1", "a5"]);
  assert.equal(saved.a1, "half a message");
});

test("a draft's own whitespace is preserved — only the emptiness test is trimmed", () => {
  // `String(d[k] || '').trim()` decides WHETHER to keep it; the stored value is `d[k]` itself. Storing
  // the trimmed copy would move the caret and eat a deliberate trailing space mid-sentence.
  state.chat = { drafts: { a1: "  leading and trailing  " } };
  const saved = withStorage((store) => {
    persistChatDrafts();
    return JSON.parse(store.get("aifyChatDrafts"));
  });
  assert.equal(saved.a1, "  leading and trailing  ");
});

test("no drafts at all persists an empty object rather than throwing", () => {
  state.chat = {};
  const saved = withStorage((store) => {
    persistChatDrafts();
    return JSON.parse(store.get("aifyChatDrafts"));
  });
  assert.deepEqual(saved, {});
});

test("persisting drafts never throws when storage refuses", () => {
  // Same contract as the preferences: this runs from an input handler, so a quota error must not
  // propagate into the keystroke that caused it.
  const had = "localStorage" in globalThis;
  globalThis.localStorage = { setItem: () => { throw new Error("QuotaExceeded"); } };
  try {
    state.chat = { drafts: { a1: "text" } };
    persistChatDrafts();
  } finally {
    if (!had) delete globalThis.localStorage;
  }
});

test("drafts and preferences use SEPARATE storage keys", () => {
  // They are persisted by different functions on different triggers; sharing a key would make each
  // overwrite the other's copy.
  state.chat = { drafts: { a1: "text" }, scope: "all" };
  const keys = withStorage((store) => {
    persistChatDrafts();
    persistChatPrefs();
    return [...store.keys()].sort();
  });
  assert.deepEqual(keys, ["aify.next.chatPrefs", "aifyChatDrafts"]);
});

// --- the two chat-shell toggles ----------------------------------------------------------------
//
// Both were branch bodies inside app.js's 631-line delegated click handler, which no test can reach.
// They are two lines each and still worth asserting, because both do THREE things — flip the flag,
// persist it, and re-sync the chips — and dropping any one of them is invisible until a reload. A
// toggle that flips without persisting looks like it works for the whole session.

/** Run with storage, DOM and a clean `state.chat`, restoring `state.chat` afterwards. */
function withChatToggles(run) {
  const saved = JSON.parse(JSON.stringify(state.chat ?? {}));
  const hadDoc = "document" in globalThis;
  const prevDoc = globalThis.document;
  globalThis.document = {
    querySelector: () => null,
    querySelectorAll: () => [],
    getElementById: () => null,
    // `toggleChatPeek` ends in a real `toast()`, which builds DOM nodes. Stubbed rather than mocked
    // away, so the test still proves the toggle reaches it without throwing.
    createElement: () => ({
      className: "", textContent: "", style: {}, dataset: {},
      setAttribute() {}, appendChild() {}, remove() {}, addEventListener() {},
      querySelectorAll: () => [], firstChild: null, children: [],
      classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    }),
    body: { appendChild() {}, contains: () => true },
  };
  // `toast` schedules its entrance on rAF, which Node has no notion of; run it synchronously.
  const hadRaf = "requestAnimationFrame" in globalThis;
  globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };
  try {
    return withStorage((store) => run(store));
  } finally {
    state.chat = saved;
    if (hadDoc) globalThis.document = prevDoc; else delete globalThis.document;
    if (!hadRaf) delete globalThis.requestAnimationFrame;
  }
}

test("toggleChatCompact FLIPS the flag and PERSISTS it in the same call", () => {
  withChatToggles((store) => {
    state.chat.compact = false;
    toggleChatCompact();
    assert.equal(state.chat.compact, true, "the flag flips");
    assert.match(store.get('aify.next.chatPrefs') ?? "", /"compact":true/, "…and reaches storage");

    toggleChatCompact();
    assert.equal(state.chat.compact, false, "it is a toggle, not a set");
    assert.match(store.get('aify.next.chatPrefs') ?? "", /"compact":false/);
  });
});

test("toggleChatPeek flips, persists, and reaches the toast without throwing", () => {
  // Peek mode changes whether opening a conversation marks it read, and the toast is its ONLY visible
  // indication — there is no chip for it. So the toast is not decoration to mock away: if it threw, the
  // flag would still flip and every branch AFTER this one in the click handler would never run. The DOM
  // stub is wide enough for `toast` to execute for real.
  withChatToggles((store) => {
    state.chat.peek = false;
    toggleChatPeek();
    assert.equal(state.chat.peek, true);
    assert.match(store.get('aify.next.chatPrefs') ?? "", /"peek":true/, "the flip is persisted");

    toggleChatPeek();
    assert.equal(state.chat.peek, false, "second press turns it back off");
    assert.match(store.get('aify.next.chatPrefs') ?? "", /"peek":false/);
  });
});

test("both toggles survive a page with no chat chips rendered", () => {
  // The handler fires from anywhere in the dashboard. `syncChatChips` over an empty DOM must not throw,
  // or pressing a toggle outside the chat page would break every branch after it in the handler.
  withChatToggles(() => {
    assert.doesNotThrow(() => { toggleChatCompact(); toggleChatPeek(); });
  });
});
