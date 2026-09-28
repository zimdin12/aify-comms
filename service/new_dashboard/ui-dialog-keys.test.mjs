// The confirm/prompt dialog's KEYBOARD behaviour and its focus trap.
//
// From the dashboard V8-coverage census (78 of 492 named functions never called by the suite): `ui.js`'s
// `onKey` and `focusables`. `ui.test.mjs` says outright that the dialog "is exercised live in the browser",
// which is why these two had never run — and they carry the two properties in this module that a browser test
// is least likely to catch:
//
//   * ESCAPE MUST NOT REACH THE PAGE BENEATH. The handler calls stopPropagation for a recorded reason: an
//     Escape that cancelled a confirm was ALSO dismissing the inspector behind it (review #14). A dialog that
//     cancels correctly and still leaks the key looks fine in isolation and wrong in use.
//   * `aria-modal="true"` IS A PROMISE. openDialog sets it, so it implements a real focus trap — Tab wraps at
//     the end, Shift+Tab wraps at the start, and focus returns to whatever opened the dialog. An
//     unimplemented trap is an accessibility claim the markup makes and the code does not keep.
//
// The dialog is driven, not stubbed: the DOM stub captures the document-level keydown listener openDialog
// installs (with capture) and the click handlers it wires, and each test fires the real thing.
//
// ONE MUTATION SURVIVES: deleting `if (!items.length) return;` from the Tab branch. With no focusables,
// `first`/`last` are both `undefined`, and `document.activeElement === undefined` is false for the null a real
// document reports — so no branch runs and the early return changes nothing observable. It only becomes a crash
// if `activeElement` were itself `undefined`, which no engine produces. A guard against a shape that cannot
// occur, recorded rather than covered by contorting the fixture into producing it.

import assert from "node:assert/strict";
import test from "node:test";

import { uiConfirm, uiPrompt } from "./ui.js";

// A focusable stand-in. `focusables()` filters on `disabled` and `offsetParent`, so both are settable.
function focusableEl(name, { disabled = false, hidden = false } = {}) {
  return {
    name, disabled, offsetParent: hidden ? null : {}, focused: 0,
    focus() { this.focused += 1; },
  };
}

function dialogNode(captured) {
  const node = {
    className: "", children: [], removed: false,
    setAttribute() {},
    remove() { node.removed = true; },
    appendChild(child) { node.children.push(child); return child; },
    addEventListener(type, fn) { captured.overlayHandlers.set(type, fn); },
    removeEventListener() {},
    querySelectorAll: () => captured.focusables,
    querySelector: (selector) => {
      // A confirm renders no field, and the real querySelector then returns null: a stub that handed a confirm
      // an input made openDialog focus that phantom field instead of a button.
      if (selector === ".dialog-input" && (captured.noInput || !captured.markup.includes("dialog-input"))) return null;
      if (!captured.controls.has(selector)) {
        const control = {
          value: "", focused: 0, focus() { this.focused += 1; },
          addEventListener(type, fn) { captured.controlHandlers.set(`${selector}:${type}`, fn); },
        };
        captured.controls.set(selector, control);
      }
      return captured.controls.get(selector);
    },
  };
  Object.defineProperty(node, "innerHTML", {
    get: () => captured.markup,
    set: (html) => { captured.markup = String(html); },
  });
  return node;
}

// Installs a DOM the dialog can live in and returns the captured seams. `isPrompt` decides whether
// `.dialog-input` exists, because openDialog reads it only for prompts.
function openWith(run, { focusables = [], previouslyFocused = null, noInput = false } = {}) {
  const had = ["document"].filter((g) => g in globalThis);
  assert.deepEqual(had, [], "a browser global leaked into the test environment — the seal is broken");

  const captured = {
    markup: "",
    focusables,
    // `noInput` makes `.dialog-input` absent AT OPEN TIME, which is the only moment that matters: openDialog
    // captures the input once into a closure variable, so replacing it in the map afterwards changes nothing
    // — that was the flaw in the first version of the missing-input test.
    noInput,
    controls: new Map(),
    controlHandlers: new Map(),
    overlayHandlers: new Map(),
    documentHandlers: new Map(),
    removedDocumentHandlers: [],
    overlay: null,
  };
  const body = { children: [], appendChild(child) { this.children.push(child); return child; } };
  globalThis.document = {
    createElement: () => {
      captured.overlay = dialogNode(captured);
      return captured.overlay;
    },
    body,
    activeElement: previouslyFocused,
    addEventListener(type, fn, capture) { captured.documentHandlers.set(type, { fn, capture }); },
    removeEventListener(type, fn, capture) { captured.removedDocumentHandlers.push({ type, fn, capture }); },
  };

  const pending = run();
  captured.pending = pending;
  captured.key = (key, extra = {}) => {
    const event = { key, shiftKey: false, prevented: 0, stopped: 0, target: null, ...extra };
    event.preventDefault = () => { event.prevented += 1; };
    event.stopPropagation = () => { event.stopped += 1; };
    const entry = captured.documentHandlers.get("keydown");
    assert.ok(entry, "the dialog installed no keydown listener — there is no focus trap or Escape handling");
    entry.fn(event);
    return event;
  };
  captured.click = (selector) => {
    const fn = captured.controlHandlers.get(`${selector}:click`);
    assert.ok(fn, `the dialog wired no click handler for ${selector}`);
    fn();
  };
  captured.clickOverlay = (target) => {
    const fn = captured.overlayHandlers.get("click");
    assert.ok(fn, "the overlay itself has no click handler — clicking outside cannot dismiss");
    fn({ target });
  };
  captured.settle = async () => {
    const value = await pending;
    delete globalThis.document;
    return value;
  };
  return captured;
}

// ── Escape and Enter ────────────────────────────────────────────────────────

test("ESCAPE cancels, and does NOT let the key reach the page beneath", async () => {
  // The recorded incident: Escape cancelled the confirm AND dismissed the inspector behind it. Both
  // preventDefault and stopPropagation are the fix, so both are asserted.
  const dialog = openWith(() => uiConfirm("Delete it?"));
  const event = dialog.key("Escape");
  assert.equal(await dialog.settle(), false, "Escape did not cancel the confirm");
  assert.equal(event.prevented, 1, "Escape was left to the browser's own default");
  assert.equal(event.stopped, 1, "Escape reached document-level handlers — the inspector beneath will close");
});

test("ENTER confirms, and is likewise contained", async () => {
  const dialog = openWith(() => uiConfirm("Proceed?"));
  const event = dialog.key("Enter");
  assert.equal(await dialog.settle(), true);
  assert.equal(event.prevented, 1);
  assert.equal(event.stopped, 1, "Enter reached the page beneath — a form there could submit");
});

test("SHIFT+ENTER does not confirm", async () => {
  // Shift+Enter is a newline, not a submit. In a prompt it is how an operator types a multi-line value; if it
  // confirmed, the dialog would close on the first line break.
  const dialog = openWith(() => uiConfirm("Proceed?"));
  const event = dialog.key("Enter", { shiftKey: true });
  assert.equal(event.prevented, 0, "Shift+Enter was swallowed");
  assert.equal(event.stopped, 0);
  dialog.click(".dialog-cancel");
  assert.equal(await dialog.settle(), false);
});

test("an unrelated key is left entirely alone", async () => {
  const dialog = openWith(() => uiConfirm("Proceed?"));
  const event = dialog.key("a");
  assert.equal(event.prevented, 0, "an ordinary keystroke was intercepted");
  assert.equal(event.stopped, 0);
  dialog.click(".dialog-cancel");
  await dialog.settle();
});

// ── Enter answers with the FOCUSED button ──────────────────────────────────
//
// External review, 2026-09-29: Enter confirmed even with Cancel focused. The keydown listener is on the
// document with capture, so it runs before the focused button's own activation and preventDefault stops
// that activation: whatever had focus, Enter meant Confirm. On a Delete dialog, Tab to Cancel then Enter
// deleted. These fail on that code.

const opened = () => new Promise((resolve) => setTimeout(resolve, 50)); // openDialog focuses after 30 ms

test("ENTER with Cancel focused CANCELS, and with Confirm focused confirms", async () => {
  const cancelled = openWith(() => uiConfirm("Delete it?"));
  globalThis.document.activeElement = cancelled.controls.get(".dialog-cancel");
  cancelled.key("Enter");
  assert.equal(await cancelled.settle(), false, "Enter on the focused Cancel button confirmed");

  // CONTROL: the same dialog with Confirm focused confirms, so the answer follows focus and not a fixed side.
  const confirmed = openWith(() => uiConfirm("Delete it?"));
  globalThis.document.activeElement = confirmed.controls.get(".dialog-confirm");
  confirmed.key("Enter");
  assert.equal(await confirmed.settle(), true);

  const prompt = openWith(() => uiPrompt("New name?"));
  prompt.controls.get(".dialog-input").value = "typed";
  globalThis.document.activeElement = prompt.controls.get(".dialog-cancel");
  prompt.key("Enter");
  assert.equal(await prompt.settle(), null, "Enter on a prompt's focused Cancel submitted the field");
});

test("a DESTRUCTIVE confirm opens on Cancel, and a stray Enter takes the safe answer", async () => {
  const danger = openWith(() => uiConfirm("Delete channel #ops?", { tone: "danger" }));
  await opened();
  assert.equal(danger.controls.get(".dialog-cancel").focused, 1, "a destructive confirm did not open with Cancel focused");
  assert.equal(danger.controls.get(".dialog-confirm").focused, 0, "a destructive confirm opened with Delete focused");
  globalThis.document.activeElement = null;  // focus is on neither button: Enter must not delete
  danger.key("Enter");
  assert.equal(await danger.settle(), false, "a stray Enter confirmed a destructive dialog");

  // CONTROL: an ordinary confirm still opens on Confirm and a stray Enter still confirms, so the two above
  // come from the tone and not from a dialog that stopped confirming on Enter.
  const plain = openWith(() => uiConfirm("Close this run?"));
  await opened();
  assert.equal(plain.controls.get(".dialog-confirm").focused, 1);
  plain.key("Enter");
  assert.equal(await plain.settle(), true);

  // And the operator can still confirm a destructive one from the keyboard, by focusing its button.
  const chosen = openWith(() => uiConfirm("Delete channel #ops?", { tone: "danger" }));
  globalThis.document.activeElement = chosen.controls.get(".dialog-confirm");
  chosen.key("Enter");
  assert.equal(await chosen.settle(), true, "Enter on a focused Delete button did not delete");
});

// ── the focus trap ──────────────────────────────────────────────────────────

test("TAB at the last control wraps to the first", async () => {
  // What `aria-modal` promises. Without it Tab walks out of the dialog and into the page behind, where the
  // operator can activate controls the modal is supposed to be blocking.
  const first = focusableEl("cancel");
  const last = focusableEl("confirm");
  const dialog = openWith(() => uiConfirm("Proceed?"), {
    focusables: [first, last], previouslyFocused: null,
  });
  globalThis.document.activeElement = last;

  const event = dialog.key("Tab");
  assert.equal(event.prevented, 1, "Tab was left to the browser, which would leave the dialog");
  assert.equal(first.focused, 1, "focus did not wrap to the first control");
  assert.equal(last.focused, 0);
  dialog.click(".dialog-cancel");
  await dialog.settle();
});

test("SHIFT+TAB at the first control wraps to the last", async () => {
  const first = focusableEl("cancel");
  const last = focusableEl("confirm");
  const dialog = openWith(() => uiConfirm("Proceed?"), { focusables: [first, last] });
  globalThis.document.activeElement = first;

  const event = dialog.key("Tab", { shiftKey: true });
  assert.equal(event.prevented, 1);
  assert.equal(last.focused, 1, "Shift+Tab did not wrap backwards to the last control");
  dialog.click(".dialog-cancel");
  await dialog.settle();
});

test("TAB in the MIDDLE is left to the browser", async () => {
  // The trap only intervenes at the ends. Preventing every Tab would break normal movement inside the dialog.
  const first = focusableEl("a");
  const middle = focusableEl("b");
  const last = focusableEl("c");
  const dialog = openWith(() => uiConfirm("Proceed?"), { focusables: [first, middle, last] });
  globalThis.document.activeElement = middle;

  const event = dialog.key("Tab");
  assert.equal(event.prevented, 0, "Tab from the middle was hijacked");
  assert.equal(first.focused + last.focused, 0, "focus was moved when it should not have been");
  dialog.click(".dialog-cancel");
  await dialog.settle();
});

test("DISABLED and HIDDEN controls are not part of the trap", async () => {
  // `focusables()` filters them out, which matters at the ends: wrapping onto a disabled button leaves focus
  // nowhere, and the operator cannot tell why Tab stopped working.
  const visible = focusableEl("ok");
  const disabled = focusableEl("busy", { disabled: true });
  const hidden = focusableEl("offscreen", { hidden: true });
  const dialog = openWith(() => uiConfirm("Proceed?"), { focusables: [visible, disabled, hidden] });
  globalThis.document.activeElement = visible;

  // With only ONE eligible control, `visible` is both first and last: Tab wraps to itself.
  const event = dialog.key("Tab");
  assert.equal(event.prevented, 1);
  assert.equal(visible.focused, 1, "the only eligible control was not focused");
  assert.equal(disabled.focused + hidden.focused, 0, "focus went to a control the operator cannot use");
  dialog.click(".dialog-cancel");
  await dialog.settle();
});

test("TAB with nothing focusable does not throw or intervene", async () => {
  const dialog = openWith(() => uiConfirm("Proceed?"), { focusables: [] });
  const event = dialog.key("Tab");
  assert.equal(event.prevented, 0, "an empty trap still swallowed the key");
  dialog.click(".dialog-cancel");
  await dialog.settle();
});

// ── settling ────────────────────────────────────────────────────────────────

test("the dialog settles ONCE, however many times it is answered", async () => {
  // `settled` guards a real sequence: Escape while a click is already in flight, or an operator hitting Enter
  // on a button that has already been pressed. A second resolve would be swallowed, but the overlay would be
  // removed twice and focus restored twice.
  const previous = focusableEl("trigger");
  const dialog = openWith(() => uiConfirm("Proceed?"), { previouslyFocused: previous });
  dialog.key("Escape");
  dialog.key("Enter");
  dialog.click(".dialog-confirm");

  assert.equal(await dialog.settle(), false, "a later answer overwrote the first one");
  assert.equal(previous.focused, 1, "focus was restored more than once");
});

test("closing REMOVES the overlay and unhooks the key listener", async () => {
  // The listener is document-level and installed with capture. Left behind, every later Escape on the page
  // runs a handler for a dialog that is gone.
  const dialog = openWith(() => uiConfirm("Proceed?"));
  const installed = dialog.documentHandlers.get("keydown");
  dialog.key("Escape");
  await dialog.settle();

  assert.equal(dialog.overlay.removed, true, "the overlay was left in the DOM");
  const removed = dialog.removedDocumentHandlers.find((r) => r.type === "keydown");
  assert.ok(removed, "the keydown listener was never removed");
  assert.equal(removed.fn, installed.fn, "a DIFFERENT function was removed — the real one is still attached");
  assert.equal(removed.capture, installed.capture,
    "removed with a different capture flag, which does not detach it");
});

test("focus returns to whatever opened the dialog", async () => {
  const previous = focusableEl("trigger");
  const dialog = openWith(() => uiConfirm("Proceed?"), { previouslyFocused: previous });
  dialog.click(".dialog-cancel");
  await dialog.settle();
  assert.equal(previous.focused, 1, "focus was not returned to the trigger");
});

test("a trigger that cannot be focused does not break closing", async () => {
  // `document.activeElement` can be null, or an element with no focus() (body in some engines). The restore is
  // wrapped for that reason, and a throw there would leave the promise unresolved with the overlay removed.
  const dialog = openWith(() => uiConfirm("Proceed?"), { previouslyFocused: { name: "no-focus-method" } });
  dialog.click(".dialog-cancel");
  assert.equal(await dialog.settle(), false);
});

test("clicking the BACKDROP cancels, clicking inside does not", async () => {
  // The inside-click must be asserted to leave the dialog OPEN. Firing both and checking the result cannot
  // distinguish them: the `settled` guard makes the second click a no-op, so an overlay that dismissed on any
  // click still resolves false and the test passes.
  const dialog = openWith(() => uiConfirm("Proceed?"));

  dialog.clickOverlay({ name: "the dialog box itself" });   // a click INSIDE the dialog
  const stillOpen = await Promise.race([
    dialog.pending.then(() => "settled"),
    new Promise((resolve) => setTimeout(() => resolve("open"), 30)),
  ]);
  assert.equal(stillOpen, "open", "a click inside the dialog dismissed it");

  dialog.clickOverlay(dialog.overlay);                       // the backdrop itself
  assert.equal(await dialog.settle(), false, "the backdrop click did not cancel");
});

// ── prompts ─────────────────────────────────────────────────────────────────

test("a prompt resolves the INPUT's value on Enter, and null on Escape", async () => {
  const dialog = openWith(() => uiPrompt("New name?", { defaultValue: "old" }));
  dialog.controls.get(".dialog-input").value = "typed name";
  dialog.key("Enter");
  assert.equal(await dialog.settle(), "typed name");

  const cancelled = openWith(() => uiPrompt("New name?"));
  cancelled.key("Escape");
  assert.equal(await cancelled.settle(), null,
    "a cancelled prompt must be null — an empty string is a value the operator typed");
});

test("a prompt with its input missing still resolves rather than hanging", async () => {
  // `input ? input.value : ''`. The markup always contains one, but the dialog must not deadlock if a future
  // change removes it — an unresolved promise here freezes whatever awaited the prompt, with the overlay gone.
  const dialog = openWith(() => uiPrompt("New name?"), { noInput: true });
  dialog.key("Enter");
  assert.equal(await dialog.settle(), "");
});

// ── what the operator is shown ──────────────────────────────────────────────

test("the message and labels are ESCAPED into the dialog", async () => {
  // The message can carry an agent id, a filename, or a run summary — none of it authored here.
  const dialog = openWith(() => uiConfirm('<img src=x onerror="alert(1)">', {
    title: "<b>T</b>", confirmLabel: "<i>Yes</i>", tone: "danger",
  }));
  assert.ok(!dialog.markup.includes("<img src=x"), "raw markup from the message survived into the dialog");
  assert.ok(dialog.markup.includes("&lt;img"), "the message was not escaped");
  assert.ok(!dialog.markup.includes("<b>T</b>"), "a raw title tag survived");
  assert.ok(!dialog.markup.includes("<i>Yes</i>"), "a raw label tag survived");
  assert.match(dialog.markup, /dialog-danger/, "the danger tone was not applied");
  assert.match(dialog.markup, /aria-modal="true"/, "the dialog does not announce itself as modal");
  dialog.key("Escape");
  await dialog.settle();
});
