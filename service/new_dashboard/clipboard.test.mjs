// Real tests for the clipboard paths.
//
// The fallback is the point. `navigator.clipboard` is unavailable outside a secure context, and this
// dashboard is routinely opened over plain HTTP on a LAN address — so the textarea + `execCommand` path is
// not a legacy nicety, it is the one that actually runs for many operators. Nothing tested it while this
// lived in app.js.
//
// SEALING. `navigator`, `window` and `document` do not exist in Node; each test installs exactly what it
// needs and removes it afterwards, so a path cannot pass because the host happened to provide something.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import { copyActiveConsole, copyText } from "./clipboard.mjs";

// ASYNC, and `return await run(...)` rather than `return run(...)`. Without the await, the `finally`
// below runs the moment the callback returns its PROMISE, tearing down `document` while the copy is
// still in flight — the continuation then failed with 'document is not defined' inside `toast`. A
// teardown that races the code under test is a harness bug that reads exactly like a product bug.
async function withEnv({ clipboard = null, secure = false, execResult = true, execThrows = false } = {}, run) {
  const had = { n: "navigator" in globalThis, w: "window" in globalThis, d: "document" in globalThis };
  const priorNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  const appended = [];
  // Node 22 ships a REAL `globalThis.navigator` defined as a getter, so a plain assignment throws
  // "Cannot set property navigator of #<Object> which has only a getter". Define over it and restore the
  // original descriptor afterwards — this test must not leave a fake navigator behind for other files.
  Object.defineProperty(globalThis, "navigator", {
    value: clipboard ? { clipboard } : {}, configurable: true, writable: true,
  });
  globalThis.window = { isSecureContext: secure };
  globalThis.requestAnimationFrame = (fn) => fn();
  // A permissive element: `copyActiveConsole` reports its result through `toast()`, which builds a real
  // DOM node of its own. Only the textarea's own fields are asserted on; the rest exist so the toast can
  // run without the test having to mock the whole of ui.js.
  const makeEl = () => {
    const kids = [];
    return {
      value: "", style: {}, textContent: "", className: "",
      children: kids,
      get firstElementChild() { return kids[0] || null; },
      select() { this.selected = true; },
      setAttribute() {}, removeAttribute() {}, remove() {}, addEventListener() {}, focus() {},
      classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
      appendChild: (child) => { kids.push(child); return child; },
    };
  };
  globalThis.document = {
    createElement: makeEl,
    getElementById: () => null,
    querySelector: () => null,
    body: {
      appendChild: (el) => { appended.push(el); return el; },
      removeChild: (el) => { appended.splice(appended.indexOf(el), 1); return el; },
    },
    execCommand: () => { if (execThrows) throw new Error("denied"); return execResult; },
  };
  try {
    return await run({ appended });
  } finally {
    if (priorNavigator) Object.defineProperty(globalThis, "navigator", priorNavigator);
    else delete globalThis.navigator;
    if (!had.w) delete globalThis.window;
    if (!had.d) delete globalThis.document;
    delete globalThis.requestAnimationFrame;
  }
}

test("empty text is refused without touching the clipboard at all", async () => {
  let called = 0;
  await withEnv({ clipboard: { writeText: async () => { called += 1; } }, secure: true }, async () => {
    for (const empty of ["", null, undefined, 0]) {
      assert.equal(await copyText(empty), false, `"${empty}" must not be copied`);
    }
  });
  assert.equal(called, 0);
});

test("a secure context uses the async clipboard API", async () => {
  let written = null;
  const ok = await withEnv(
    { clipboard: { writeText: async (t) => { written = t; } }, secure: true },
    () => copyText("hello"),
  );
  assert.equal(ok, true);
  assert.equal(written, "hello");
});

test("an INSECURE context skips the API and uses the textarea fallback", async () => {
  // The case that matters: http:// on a LAN address, where navigator.clipboard exists but is unusable.
  let apiCalls = 0;
  const ok = await withEnv(
    { clipboard: { writeText: async () => { apiCalls += 1; } }, secure: false, execResult: true },
    () => copyText("hello"),
  );
  assert.equal(apiCalls, 0, "the API must not be attempted outside a secure context");
  assert.equal(ok, true, "the fallback must still succeed");
});

test("a clipboard API that REJECTS falls through to the textarea rather than failing", async () => {
  const ok = await withEnv(
    { clipboard: { writeText: async () => { throw new Error("denied"); } }, secure: true, execResult: true },
    () => copyText("hello"),
  );
  assert.equal(ok, true, "a permission denial must not be the end of the attempt");
});

test("the fallback cleans up its off-screen textarea, on success and on failure", async () => {
  await withEnv({ secure: false, execResult: true }, async ({ appended }) => {
    await copyText("hello");
    assert.deepEqual(appended, [], "a leaked textarea would accumulate one node per copy");
  });
  await withEnv({ secure: false, execResult: false }, async ({ appended }) => {
    assert.equal(await copyText("hello"), false, "execCommand returning false must be reported as failure");
    assert.deepEqual(appended, [], "…and must still clean up");
  });
});

test("copyText never throws, whatever the environment does", async () => {
  assert.equal(await withEnv({ secure: false, execThrows: true }, () => copyText("hello")), false);

  // No document at all — a copy button that raises is worse than one that reports failure.
  const hadWindow = "window" in globalThis;
  const prior = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  Object.defineProperty(globalThis, "navigator", { value: {}, configurable: true, writable: true });
  globalThis.window = { isSecureContext: false };
  delete globalThis.document;
  try {
    assert.equal(await copyText("hello"), false);
  } finally {
    if (prior) Object.defineProperty(globalThis, "navigator", prior);
    else delete globalThis.navigator;
    if (!hadWindow) delete globalThis.window;
  }
});

function fakeTerm({ hasSelection = false, selection = "" } = {}) {
  const calls = [];
  return {
    calls,
    hasSelection: () => hasSelection,
    getSelection: () => selection,
    selectAll() { calls.push("selectAll"); },
    clearSelection() { calls.push("clearSelection"); },
  };
}

test("copying the console uses the operator's selection when there is one", async () => {
  const term = fakeTerm({ hasSelection: true, selection: "picked text" });
  state.activeXterm = { term };
  let copied = null;
  await withEnv({ clipboard: { writeText: async (t) => { copied = t; } }, secure: true }, async () => {
    copyActiveConsole();
    await new Promise((r) => setTimeout(r, 0));
  });
  assert.equal(copied, "picked text");
  assert.deepEqual(term.calls, [], "an existing selection must not be replaced or cleared");
});

test("with no selection it selects all, copies, and CLEARS the selection again", async () => {
  // Without the clear, copy-all leaves the whole console highlighted — invisible in the source, obvious
  // on screen, and the reason that branch exists.
  const term = fakeTerm({ hasSelection: false, selection: "whole buffer" });
  state.activeXterm = { term };
  let copied = null;
  await withEnv({ clipboard: { writeText: async (t) => { copied = t; } }, secure: true }, async () => {
    copyActiveConsole();
    await new Promise((r) => setTimeout(r, 0));
  });
  assert.equal(copied, "whole buffer");
  assert.deepEqual(term.calls, ["selectAll", "clearSelection"]);
});

test("copying the console is a no-op when no console is open", () => {
  for (const empty of [null, undefined, {}, { term: null }]) {
    state.activeXterm = empty;
    copyActiveConsole();
  }
});
