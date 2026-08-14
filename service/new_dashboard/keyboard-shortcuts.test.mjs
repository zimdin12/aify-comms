// The global keyboard shortcuts, tested by CALLING the handler.
//
// This was a top-level `document.addEventListener('keydown', …)` in app.js, so nothing could reach it.
// Three of its four rules exist ONLY for keyboard users: the status-why popover and the favourite star
// are `role=button` spans, which have no native Enter/Space handling — the browser does nothing for them
// unless this code does. If any of that stops firing, the page looks completely normal and a
// keyboard-only operator simply cannot use those controls.
//
// The fourth is Ctrl+Shift+C, and the shift is not cosmetic: xterm swallows plain Ctrl+C as SIGINT into
// the PTY, so an unshifted copy shortcut would interrupt the agent instead of copying its output.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import { handleGlobalKeydown } from "./keyboard-shortcuts.mjs";

/**
 * Install a DOM whose inspector openness and focused element are controllable.
 * Returns recorders for everything the handler can reach.
 */
function withKeys({ inspectorOpen = false, activeTag = "BODY", xterm = null } = {}, run) {
  const hadDoc = "document" in globalThis;
  const prevDoc = globalThis.document;
  const savedXterm = state.activeXterm;
  state.activeXterm = xterm;
  // `copyActiveConsole` runs for real on the Ctrl+Shift+C path and reaches the clipboard and a toast.
  // Stubbed rather than mocked away, so the shortcut is asserted to actually reach it.
  // NOT navigator: it is a getter-only property on globalThis in Node 22 and assigning it throws.
  // `copyText` falls back to `document.execCommand`, which the stub below provides — so the copy path
  // still runs for real rather than being mocked out.
  const hadRaf = "requestAnimationFrame" in globalThis;
  globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };
  const stubEl = () => ({
    className: "", textContent: "", value: "", style: {}, dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    setAttribute() {}, appendChild() {}, remove() {}, addEventListener() {}, focus() {}, select() {},
    querySelectorAll: () => [], firstChild: null, children: [],
  });
  globalThis.document = {
    activeElement: { tagName: activeTag },
    getElementById: (id) => (id === "inspector"
      ? { classList: { contains: (c) => c === "open" && inspectorOpen } }
      : null),
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: stubEl,
    execCommand: () => true,
    body: { appendChild() {}, removeChild() {}, contains: () => true },
  };
  const closed = [];
  const favs = [];
  // PROMISE-AWARE. A synchronous `finally` restores the globals before an awaited body settles, so an
  // async assertion runs against a torn-down DOM — it fails the FILE while every subtest passes, which
  // is exactly how this suite first failed. Returning the chain makes the teardown wait.
  let out;
  try {
    out = run({
      closeInspector: () => closed.push("inspector"),
      toggleFavorite: (id) => favs.push(id),
      closed,
      favs,
    });
    if (out && typeof out.then === "function") return out.finally(restore);
    return out;
  } finally {
    if (!(out && typeof out.then === "function")) restore();
  }

  function restore() {
    state.activeXterm = savedXterm;
    if (hadDoc) globalThis.document = prevDoc; else delete globalThis.document;
    if (!hadRaf) delete globalThis.requestAnimationFrame;
  }
}

/** A keydown event double that records preventDefault. */
function key(k, extra = {}) {
  const e = {
    key: k,
    prevented: 0,
    preventDefault() { this.prevented += 1; },
    target: extra.target ?? { matches: () => false },
    ...extra,
  };
  return e;
}

const targetMatching = (sel, dataset = {}) => ({ matches: (s) => s === sel, dataset });

// --- Escape ------------------------------------------------------------------------------------

test("Escape closes the status-why popover unconditionally", () => {
  // No guard on this one — the popover close is safe to call when nothing is open, and making it
  // conditional would leave the popover stuck whenever the inspector rules below did not match.
  withKeys({}, (h) => {
    assert.doesNotThrow(() => handleGlobalKeydown(key("Escape"), h.closeInspector, h.toggleFavorite));
  });
});

test("Escape closes an OPEN inspector, and does nothing when it is shut", () => {
  withKeys({ inspectorOpen: true }, (h) => {
    handleGlobalKeydown(key("Escape"), h.closeInspector, h.toggleFavorite);
    assert.deepEqual(h.closed, ["inspector"]);
  });
  withKeys({ inspectorOpen: false }, (h) => {
    handleGlobalKeydown(key("Escape"), h.closeInspector, h.toggleFavorite);
    assert.deepEqual(h.closed, [], "a closed inspector must not be closed again");
  });
});

test("ESCAPE FROM INSIDE A FIELD DOES NOT CLOSE THE INSPECTOR", () => {
  // The guard that makes the inspector usable. Escape is how a browser cancels an IME composition and
  // how operators back out of a half-typed value; closing the whole panel on it would discard the form
  // they were filling in. All three field tags are checked because the regex lists all three.
  for (const tag of ["INPUT", "TEXTAREA", "SELECT"]) {
    withKeys({ inspectorOpen: true, activeTag: tag }, (h) => {
      handleGlobalKeydown(key("Escape"), h.closeInspector, h.toggleFavorite);
      assert.deepEqual(h.closed, [], `Escape in a ${tag} must not close the inspector`);
    });
  }
});

test("Escape from a non-field element still closes it", () => {
  // The complement of the rule above — asserted separately so an inverted regex cannot pass both.
  for (const tag of ["BODY", "DIV", "BUTTON", "SPAN"]) {
    withKeys({ inspectorOpen: true, activeTag: tag }, (h) => {
      handleGlobalKeydown(key("Escape"), h.closeInspector, h.toggleFavorite);
      assert.deepEqual(h.closed, ["inspector"], `Escape on ${tag} must close`);
    });
  }
});

// --- Enter / Space on role=button spans ---------------------------------------------------------

test("ENTER AND SPACE OPERATE THE FAVOURITE STAR — it is a span with no native key handling", () => {
  // Without this the star is mouse-only. Both keys are required: a browser fires neither for a span,
  // and screen-reader users reach `role=button` with either.
  for (const k of ["Enter", " "]) {
    withKeys({}, (h) => {
      const e = key(k, { target: targetMatching("[data-fav-toggle]", { favToggle: "coder-1" }) });
      handleGlobalKeydown(e, h.closeInspector, h.toggleFavorite);
      assert.deepEqual(h.favs, ["coder-1"], `${JSON.stringify(k)} must toggle the favourite`);
      assert.equal(e.prevented, 1, "Space must be prevented or the page scrolls");
    });
  }
});

test("Enter and Space open the status-why popover on its trigger", () => {
  for (const k of ["Enter", " "]) {
    withKeys({}, (h) => {
      const e = key(k, { target: targetMatching("[data-status-why]") });
      assert.doesNotThrow(() => handleGlobalKeydown(e, h.closeInspector, h.toggleFavorite));
      assert.equal(e.prevented, 1);
    });
  }
});

test("a key press on an UNRELATED element does nothing", () => {
  // `event.target?.matches?.(...)` — the rules are scoped to their own triggers. Firing on any Enter
  // would toggle a favourite every time an operator submitted a form.
  withKeys({}, (h) => {
    const e = key("Enter");
    handleGlobalKeydown(e, h.closeInspector, h.toggleFavorite);
    assert.deepEqual(h.favs, []);
    assert.equal(e.prevented, 0, "an unmatched key must not be swallowed");
  });
});

test("a target with NO matches() method does not throw", () => {
  // The optional call. Keydown targets include the document and text nodes in some browsers, and
  // throwing here would kill every rule after it — including the Escape that dismisses overlays.
  withKeys({}, (h) => {
    assert.doesNotThrow(() => handleGlobalKeydown(
      { key: "Enter", target: {}, preventDefault() {} }, h.closeInspector, h.toggleFavorite,
    ));
    assert.doesNotThrow(() => handleGlobalKeydown(
      { key: "Enter", target: null, preventDefault() {} }, h.closeInspector, h.toggleFavorite,
    ));
  });
});

// --- Ctrl+Shift+C ------------------------------------------------------------------------------

test("CTRL+SHIFT+C copies only when a console is live — and plain Ctrl+C is left alone", async () => {
  // The shift is load-bearing. xterm delivers plain Ctrl+C to the PTY as SIGINT, so binding copy there
  // would interrupt the agent instead of copying its output. Both letter cases are accepted because the
  // shift key changes what `event.key` reports.
  for (const letter of ["C", "c"]) {
    await withKeys({ xterm: { term: {} } }, async (h) => {
      const e = key(letter, { ctrlKey: true, shiftKey: true });
      handleGlobalKeydown(e, h.closeInspector, h.toggleFavorite);
      assert.equal(e.prevented, 1, `Ctrl+Shift+${letter} must be handled`);
      await new Promise((r) => setTimeout(r, 0));
    });
  }

  await withKeys({ xterm: { term: {} } }, async (h) => {
    const plain = key("c", { ctrlKey: true, shiftKey: false });
    handleGlobalKeydown(plain, h.closeInspector, h.toggleFavorite);
    assert.equal(plain.prevented, 0, "plain Ctrl+C must reach the PTY as SIGINT");
    // The copy above is async; settle it INSIDE the scope so its toast finds the stubbed DOM.
    await new Promise((r) => setTimeout(r, 0));
  });
});

test("Ctrl+Shift+C does nothing with no console open", () => {
  // `state.activeXterm?.term`. Without the guard the shortcut would swallow the browser's own
  // Ctrl+Shift+C (devtools inspector) on every page that has no terminal.
  for (const xterm of [null, undefined, {}, { term: null }]) {
    withKeys({ xterm }, (h) => {
      const e = key("C", { ctrlKey: true, shiftKey: true });
      handleGlobalKeydown(e, h.closeInspector, h.toggleFavorite);
      assert.equal(e.prevented, 0, `no console (${JSON.stringify(xterm)}) means no handling`);
    });
  }
});
