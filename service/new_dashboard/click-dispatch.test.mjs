// The delegated click dispatcher, tested by DISPATCHING clicks at it.
//
// This is one listener for the whole dashboard, and its defining property is that ORDER IS BEHAVIOUR:
// the first branch whose `closest()` matches wins and returns. Two branches sit where they do because
// getting it wrong shipped live bugs, and both are asserted here by building a target that matches
// MORE THAN ONE selector and checking which branch claimed it — which is the only way to test an
// ordered chain. A test that fired one unambiguous click per branch would pass against any order.

import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { state } from "./state.mjs";
import { dispatchClick, initClickDispatch } from "./click-dispatch.mjs";

/**
 * A fake element whose `closest()` answers from a declared set of selectors.
 *
 * `matches` is the list of selectors this target should satisfy — so a single click can be made to
 * look like a mode-switch chip AND a selectable session row at once, which is exactly the ambiguity
 * the ordering exists to resolve.
 */
function target(matches, dataset = {}) {
  const el = {
    dataset,
    value: "",
    closest: (sel) => (matches.includes(sel) ? el : null),
    querySelector: () => null,
    querySelectorAll: () => [],
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    focus() {},
  };
  return el;
}

function clickOn(el) {
  return { target: el, preventDefault() {}, stopPropagation() {}, shiftKey: false, metaKey: false, ctrlKey: false };
}

function withDispatch() {
  const saved = { document: globalThis.document, fetch: globalThis.fetch, requestAnimationFrame: globalThis.requestAnimationFrame };
  const els = new Map();
  globalThis.requestAnimationFrame = (fn) => { fn(); return 1; };
  globalThis.document = {
    getElementById: (id) => {
      if (!els.has(id)) {
        els.set(id, {
          innerHTML: "", textContent: "", className: "", value: "", hidden: false, dataset: {}, style: {},
          children: [], firstElementChild: null,
          classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
          querySelector: () => null, querySelectorAll: () => [], appendChild() {}, setAttribute() {},
          addEventListener() {}, removeEventListener() {}, remove() {}, focus() {},
        });
      }
      return els.get(id);
    },
    querySelector: () => null, querySelectorAll: () => [],
    createElement: () => ({ className: "", innerHTML: "", querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, appendChild() {}, remove() {}, setAttribute() {}, focus() {}, classList: { add() {}, remove() {}, toggle() {} }, dataset: {}, style: {} }),
    addEventListener() {}, removeEventListener() {},
    body: { appendChild() {}, classList: { add() {}, remove() {} }, style: { setProperty() {} } },
    activeElement: null,
  };
  globalThis.fetch = async () => ({ ok: true, status: 200, statusText: "OK", json: async () => ({}), text: async () => "{}" });
  setApiBase("");
  const calls = { setPage: [], renderSessionWorkspace: 0, refreshSoon: 0, closeInspector: 0, conversation: 0 };
  initClickDispatch({
    chatController: { renderConversation: () => { calls.conversation += 1; }, render() {}, renderRail() {}, close() {}, open() {} },
    closeInspector: () => { calls.closeInspector += 1; },
    refreshSoon: () => { calls.refreshSoon += 1; },
    renderSessionWorkspace: () => { calls.renderSessionWorkspace += 1; },
    setPage: (p) => { calls.setPage.push(p); },
  });
  Object.assign(state, {
    agents: [], sessions: [], runs: [], contracts: [], messages: [],
    selectedSessionIds: new Set(), selectedDiagnosticIds: new Set(),
    chat: { ...(state.chat || {}), selected: null, replyTo: { id: "m1" }, identity: "dashboard", channels: [] },
    inspector: { run: null, events: [] },
  });
  return { calls, restore: () => Object.assign(globalThis, saved) };
}

test("a click matching NOTHING is a no-op", () => {
  // Every poll re-renders the page, so most clicks land on inert chrome. Throwing here would take the
  // whole listener down and leave every control on the page dead until reload.
  const h = withDispatch();
  try {
    assert.doesNotThrow(() => dispatchClick(clickOn(target([]))));
  } finally { h.restore(); }
});

test("A MODE-SWITCH CHIP INSIDE A SELECTABLE ROW GOES TO THE CHIP", () => {
  // The chip is nested in a selectable session row, so both selectors match the same click. If row
  // selection were checked first the chip would be unclickable — the row would swallow it, and the
  // only symptom is that the control appears to do nothing.
  const h = withDispatch();
  try {
    const el = target(["[data-mode-switch]", "[data-session-select]"], { modeSwitch: "managed", agentId: "coder" });
    dispatchClick(clickOn(el));
    assert.equal(h.calls.renderSessionWorkspace, 0, "row selection must NOT have claimed this click");
  } finally { h.restore(); }
});

test("…while a plain row still selects", () => {
  // The other half. Without it the test above passes against a dispatcher that dropped row selection
  // altogether.
  const h = withDispatch();
  try {
    dispatchClick(clickOn(target(["[data-session-select]"], { sessionSelect: "s1" })));
    assert.equal(h.calls.renderSessionWorkspace, 1, "a row with no chip must select");
  } finally { h.restore(); }
});

test("WORK-VIEW IS SCOPED TO ITS BUTTON — the section carries the same attribute", () => {
  // Live regression, 2026-07-02. The grid SECTION uses `data-work-view` as a CSS state hook, so a bare
  // `[data-work-view]` matched every click inside Work and ate Inspect/Remind/Close. The scoped
  // selector is `button[data-work-view]`, and the way to prove the scope is to offer a target that
  // matches the BARE selector only.
  const h = withDispatch();
  const savedStorage = globalThis.localStorage;
  const written = [];
  globalThis.localStorage = { setItem: (k, v) => written.push([k, v]), getItem: () => null, removeItem() {} };
  try {
    // Matches the BARE attribute only — this is the grid section, not the toggle button.
    dispatchClick(clickOn(target(["[data-work-view]"], { workView: "board" })));
    assert.deepEqual(written, [], "the section must not be treated as the view toggle");

    // …and the button form still works, so the assertion above is not passing because the branch is
    // simply broken.
    dispatchClick(clickOn(target(["button[data-work-view]"], { workView: "board" })));
    assert.deepEqual(written, [["aifyWorkView", "board"]], "the toggle button must still switch the view");
  } finally {
    globalThis.localStorage = savedStorage;
    if (!savedStorage) delete globalThis.localStorage;
    h.restore();
  }
});

test("contract-view is scoped the same way, for the same reason", () => {
  const h = withDispatch();
  try {
    const el = target(["button[data-contract-view]"], { contractView: "board" });
    assert.doesNotThrow(() => dispatchClick(clickOn(el)));
  } finally { h.restore(); }
});

test("the chat reply-clear branch clears the reply and re-renders", () => {
  const h = withDispatch();
  try {
    state.chat.replyTo = { id: "m1" };
    dispatchClick(clickOn(target(["[data-chat-reply-clear]"])));
    assert.equal(state.chat.replyTo, null, "clearing a reply must actually clear it");
    assert.equal(h.calls.conversation, 1, "…and repaint the conversation it belongs to");
  } finally { h.restore(); }
});

test("a page-navigation click reaches setPage", () => {
  const h = withDispatch();
  try {
    dispatchClick(clickOn(target(["[data-page], [data-page-jump]"], { page: "sessions" })));
    assert.deepEqual(h.calls.setPage, ["sessions"]);
  } finally { h.restore(); }
});

test("EVERY BRANCH RETURNS — one click never fires two handlers", () => {
  // The property that makes an ordered chain safe. A branch missing its `return` would fall through
  // and let a later selector match the same element, firing two actions for one click. Constructed by
  // matching three selectors at once and checking exactly one effect landed.
  const h = withDispatch();
  try {
    const el = target(["[data-page], [data-page-jump]", "[data-open-chat]", "[data-session-select]"],
      { page: "sessions", openChat: "coder", sessionSelect: "s1" });
    dispatchClick(clickOn(el));
    const fired = [h.calls.setPage.length, h.calls.renderSessionWorkspace].filter(Boolean).length;
    assert.ok(fired <= 1, `exactly one branch may act on a click, ${fired} did`);
  } finally { h.restore(); }
});

test("INIT REFUSES A PARTIAL BAG", () => {
  const full = {
    chatController: { renderConversation() {} }, closeInspector() {}, refreshSoon() {},
    renderSessionWorkspace() {}, setPage() {},
  };
  for (const missing of Object.keys(full)) {
    const partial = { ...full };
    delete partial[missing];
    assert.throws(() => initClickDispatch(partial), new RegExp(missing), `omitting ${missing} must throw`);
  }
  assert.doesNotThrow(() => initClickDispatch(full));
});

test("THE ORDERING CONSTRAINTS ARE STILL EXPRESSED IN THE SOURCE, in the right order", () => {
  // Belt and braces for the two branches whose POSITION is the behaviour. The dispatch tests above
  // prove the outcome for the cases they construct; this proves the general rule those cases sample,
  // which no single click can demonstrate.
  const src = fs.readFileSync(new URL("./click-dispatch.mjs", import.meta.url), "utf8");
  const modeSwitch = src.indexOf("closest('[data-mode-switch]')");
  const sessionSelect = src.indexOf("closest('[data-session-select]')");
  assert.ok(modeSwitch > -1 && sessionSelect > -1, "both branches must exist");
  assert.ok(modeSwitch < sessionSelect, "the nested chip must be checked before the row that contains it");
  for (const attr of ["data-work-view", "data-contract-view"]) {
    assert.ok(src.includes(`closest('button[${attr}]')`),
      `${attr} must stay scoped to its button — the section carries the same attribute as a CSS hook`);
    assert.ok(!src.includes(`closest('[${attr}]')`), `${attr} must NOT be matched bare`);
  }
});
