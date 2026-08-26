// The prompt dialog's text field says what to type, to a screen reader as well as to an eye.
//
// THE GAP. `openDialog` renders `<p class="dialog-message">` and, for a prompt, an `<input>` beside
// it. The message is the ONLY thing that says what to enter -- "New name for coder-1", "Workspace
// path" -- and the input did not point at it. A screen reader reaching the field announced an
// unlabelled edit box: the question had been read a moment earlier as dialog content, and then the
// control the user is sitting in named nothing at all.
//
// This is not the same claim as the focus trap in `ui-dialog-keys.test.mjs`. That file proves
// `aria-modal="true"` is honoured. This one proves the field inside the modal has an accessible name.
// A dialog can trap focus perfectly and still hand the user a nameless box.
//
// WHY THE ID IS A COUNTER. `aria-labelledby` needs a real id, and two dialogs opened in one session
// must not collide -- an id reused across overlays points a live control at a removed paragraph. A
// counter makes the markup deterministic, which is what lets the last test below assert uniqueness
// instead of hoping two random values differ.

import assert from "node:assert/strict";
import test from "node:test";

import { uiPrompt, uiConfirm } from "./ui.js";

/** Opens a dialog against a minimal document and returns the markup it rendered. */
function markupFor(run) {
  assert.equal("document" in globalThis, false, "a browser global leaked in — the seal is broken");
  const captured = { markup: "" };
  const node = {
    className: "",
    setAttribute() {},
    remove() {},
    addEventListener() {},
    removeEventListener() {},
    querySelectorAll: () => [],
    querySelector: () => ({ value: "", focus() {}, addEventListener() {} }),
  };
  Object.defineProperty(node, "innerHTML", {
    get: () => captured.markup,
    set: (html) => { captured.markup = String(html); },
  });
  globalThis.document = {
    createElement: () => node,
    body: { appendChild: (child) => child },
    activeElement: null,
    addEventListener() {},
    removeEventListener() {},
  };
  try {
    run();
    return captured.markup;
  } finally {
    delete globalThis.document;
  }
}

test("the prompt renders both the message and the field", () => {
  // The control. Every assertion below reads attributes out of this markup; empty markup would
  // satisfy none of them for the wrong reason, and `assert.match` on "" fails opaquely.
  const markup = markupFor(() => uiPrompt("New name for coder-1"));
  assert.match(markup, /class="dialog-message"/, "no message element was rendered");
  assert.match(markup, /class="dialog-input"/, "no input was rendered for a prompt");
});

test("the input is named by the message that asks the question", () => {
  const markup = markupFor(() => uiPrompt("New name for coder-1"));
  const messageId = markup.match(/<p class="dialog-message" id="([^"]+)"/)?.[1];
  assert.ok(messageId, "the message carries no id, so nothing can point at it");
  const input = markup.match(/<input class="dialog-input"[^>]*>/)?.[0] ?? "";
  assert.match(
    input,
    new RegExp(`aria-labelledby="${messageId}"`),
    `the prompt input does not point at the message. A screen reader announces it as an unlabelled `
      + `edit field, and the message is the only thing that says what to type. Input was: ${input}`,
  );
});

test("the dialog itself is named too, not only the field", () => {
  // `role="dialog"` with no accessible name announces as "dialog". The message is the natural name
  // and costs nothing extra now that it has an id.
  const markup = markupFor(() => uiConfirm("Remove agent \"coder-1\"?"));
  const messageId = markup.match(/<p class="dialog-message" id="([^"]+)"/)?.[1];
  assert.ok(messageId);
  assert.match(markup, new RegExp(`role="dialog"[^>]*aria-labelledby="${messageId}"`));
});

test("two dialogs do not share one id", () => {
  // A reused id points a live control at a paragraph that has been removed from the document. The
  // counter is what makes this checkable rather than probabilistic.
  const first = markupFor(() => uiPrompt("first"));
  const second = markupFor(() => uiPrompt("second"));
  const idOf = (markup) => markup.match(/<p class="dialog-message" id="([^"]+)"/)?.[1];
  assert.ok(idOf(first) && idOf(second));
  assert.notEqual(idOf(first), idOf(second), "two dialogs rendered the same message id");
});

test("a confirm renders no input at all", () => {
  // The negative control for the first test: if `.dialog-input` appeared unconditionally, the naming
  // assertions above would pass on a dialog that has no field, proving nothing about prompts.
  const markup = markupFor(() => uiConfirm("Just checking"));
  assert.doesNotMatch(markup, /class="dialog-input"/);
});
