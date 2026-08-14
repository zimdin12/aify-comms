// Real tests for the status-why popover.
//
// Three behaviours that a source regex could only prove had been typed: the reason falls back so a chip
// with no explanation still explains itself, the position is clamped so a chip near an edge does not open
// off-screen, and focus returns to the trigger on close. The last is the accessibility contract — a dialog
// that drops focus leaves a keyboard user at the top of a 4,000-line page.
//
// SEALING. `document`, `window` and `setTimeout` behaviour are supplied per test and removed afterwards,
// so nothing here can pass by accident on a host that provides them.

import assert from "node:assert/strict";
import test from "node:test";

import { closeStatusWhy, openStatusWhy } from "./status-why-popover.mjs";

function fakePopover() {
  return {
    hidden: true,
    innerHTML: "",
    style: {},
    attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; },
    querySelector: () => ({ focus() {} }),
  };
}

function fakeTrigger({ dataset = {}, title = "", rect = { bottom: 100, left: 50 } } = {}) {
  let focused = 0;
  return {
    dataset,
    title,
    getBoundingClientRect: () => rect,
    focus() { focused += 1; },
    get focusCount() { return focused; },
  };
}

function withDom({ popover = fakePopover(), innerWidth = 1200, innerHeight = 800 } = {}, run) {
  const had = { d: "document" in globalThis, w: "window" in globalThis };
  globalThis.document = { getElementById: (id) => (id === "status-why-popover" ? popover : null) };
  globalThis.window = { innerWidth, innerHeight };
  try {
    return run(popover);
  } finally {
    if (!had.d) delete globalThis.document;
    if (!had.w) delete globalThis.window;
  }
}

test("the reason falls back from data attribute to title to a default", () => {
  withDom({}, (p) => {
    openStatusWhy(fakeTrigger({ dataset: { statusWhy: "from data", statusKind: "working" } }));
    assert.ok(p.innerHTML.includes("from data"));
    assert.ok(p.innerHTML.includes("working"), "the kind is shown as the heading");

    openStatusWhy(fakeTrigger({ title: "from title" }));
    assert.ok(p.innerHTML.includes("from title"), "a chip without the data attribute uses its title");

    openStatusWhy(fakeTrigger({}));
    assert.ok(p.innerHTML.includes("No status reason loaded."),
      "a chip with neither must still say something rather than open blank");
    assert.ok(p.innerHTML.includes("unknown"), "…and fall back to an unknown kind");
  });
});

test("the reason and kind are HTML-escaped", () => {
  // They come from server-supplied status text and land in innerHTML.
  withDom({}, (p) => {
    openStatusWhy(fakeTrigger({ dataset: { statusWhy: "<img src=x onerror=1>", statusKind: "a&b" } }));
    assert.ok(!p.innerHTML.includes("<img"), "markup in a status reason must not be rendered as markup");
    assert.ok(p.innerHTML.includes("a&amp;b"));
  });
});

test("the popover is clamped inside the viewport", () => {
  // A chip near the right or bottom edge would otherwise open a popover that is partly off-screen.
  withDom({ innerWidth: 1000, innerHeight: 700 }, (p) => {
    openStatusWhy(fakeTrigger({ rect: { bottom: 690, left: 980 } }));
    assert.equal(p.style.top, `${700 - 160}px`, "clamped up from the bottom edge");
    assert.equal(p.style.left, `${1000 - 320}px`, "clamped in from the right edge");
  });

  withDom({}, (p) => {
    openStatusWhy(fakeTrigger({ rect: { bottom: -50, left: -80 } }));
    assert.equal(p.style.top, "12px", "…and off the top/left it is clamped to a 12px margin");
    assert.equal(p.style.left, "12px");
  });
});

test("a normal position is used as-is, offset below the chip", () => {
  withDom({}, (p) => {
    openStatusWhy(fakeTrigger({ rect: { bottom: 100, left: 50 } }));
    assert.equal(p.style.top, "108px", "8px below the trigger");
    assert.equal(p.style.left, "50px");
  });
});

test("closing returns focus to the trigger that opened it", () => {
  withDom({}, (p) => {
    const trigger = fakeTrigger({ dataset: { statusWhy: "why" } });
    openStatusWhy(trigger);
    assert.equal(p.hidden, false);

    closeStatusWhy();
    assert.equal(p.hidden, true);
    assert.equal(p.innerHTML, "", "the content must be cleared, not just hidden");
    assert.equal(trigger.focusCount, 1, "focus must return to the chip, not be dropped to the document");
  });
});

test("closing twice does not re-focus a stale trigger", () => {
  // The latch is nulled on close. Without that, a later close would yank focus back to a chip the operator
  // has since navigated away from.
  withDom({}, () => {
    const trigger = fakeTrigger({});
    openStatusWhy(trigger);
    closeStatusWhy();
    closeStatusWhy();
    assert.equal(trigger.focusCount, 1);
  });
});

test("closing survives a trigger that has been removed from the DOM", () => {
  // The chip is re-rendered by the poll while the popover is open, so the remembered element can be
  // detached — or replaced by one with no focus method at all.
  withDom({}, () => {
    openStatusWhy({ dataset: {}, title: "", getBoundingClientRect: () => ({ bottom: 1, left: 1 }) });
    closeStatusWhy();
  });
});

test("opening is a no-op without a popover host or without a trigger", () => {
  const had = "document" in globalThis;
  globalThis.document = { getElementById: () => null };
  globalThis.window = { innerWidth: 100, innerHeight: 100 };
  try {
    openStatusWhy(fakeTrigger({}));
    closeStatusWhy();
  } finally {
    if (!had) { delete globalThis.document; delete globalThis.window; }
  }
  withDom({}, (p) => {
    openStatusWhy(null);
    assert.equal(p.hidden, true, "no trigger means nothing to explain");
  });
});
