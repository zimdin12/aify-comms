// Mounting an xterm against a live terminal — the dashboard's console.
//
// Extracted from app.js in v0.5.4, where nothing could reach it: 356 lines, the largest single function
// in the file. This suite does NOT drive a real mount — that needs xterm, a DOM and a live terminal —
// it covers the guards that decide whether a mount proceeds at all, which is where the generation
// counter and the injected `resyncActiveConsole` live.

import assert from "node:assert/strict";
import test from "node:test";

import { mountXtermForTerminal } from "./xterm-mount.mjs";
import { state } from "./state.mjs";

function withDom(run) {
  const had = "document" in globalThis;
  const prev = globalThis.document;
  const savedXterm = state.activeXterm;
  globalThis.document = {
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => ({
      className: "", textContent: "", style: {}, dataset: {},
      classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
      setAttribute() {}, appendChild() {}, remove() {}, addEventListener() {},
      querySelector: () => null, querySelectorAll: () => [],
    }),
    body: { appendChild() {}, contains: () => true },
  };
  try {
    return run();
  } finally {
    state.activeXterm = savedXterm;
    if (had) globalThis.document = prev; else delete globalThis.document;
  }
}

test("a mount with NO CONTAINER is refused rather than throwing", async () => {
  // The console pane is not always in the DOM — an operator on another page, or a render that has not
  // landed yet. This runs from poll-driven code, so throwing here would take the caller down on an
  // ordinary state rather than an exceptional one.
  await withDom(async () => {
    await assert.doesNotReject(() => mountXtermForTerminal("t1", "a1", null, {}, { resyncActiveConsole: async () => {} }));
    await assert.doesNotReject(() => mountXtermForTerminal("t1", "a1", undefined, {}, { resyncActiveConsole: async () => {} }));
  });
});

test("a mount with no TERMINAL id is refused", async () => {
  await withDom(async () => {
    await assert.doesNotReject(() => mountXtermForTerminal("", "a1", {}, {}, { resyncActiveConsole: async () => {} }));
  });
});

test("THE INJECTED resyncActiveConsole IS THE SEAM, and the module never imports it", async () => {
  // It reaches `refresh`, the render orchestrator app.js still owns. Importing it here would drag the
  // whole render web across — which is the reason this function could not move for the entire series.
  // Asserted structurally because the import's ABSENCE is the property.
  const src = await import("node:fs").then((fs) =>
    fs.readFileSync(new URL("./xterm-mount.mjs", import.meta.url), "utf8"));
  assert.doesNotMatch(src, /^import .*resyncActiveConsole/m, "it must be injected, never imported");
  assert.match(src, /\{ resyncActiveConsole \}\)/, "…and it arrives as a parameter");
});

test("the two counters it owns moved WITH it and are declared exactly once", async () => {
  // `_consoleMountGen` is how a mount parked awaiting a font load detects that a newer mount superseded
  // it; `consoleInputBlockedToastAt` debounces the input-blocked warning. Both are meaningless outside
  // this function, and a copy left behind in app.js would silently give the two files separate counters.
  const fs = await import("node:fs");
  const mod = fs.readFileSync(new URL("./xterm-mount.mjs", import.meta.url), "utf8");
  const app = fs.readFileSync(new URL("./app.js", import.meta.url), "utf8");
  for (const name of ["_consoleMountGen", "consoleInputBlockedToastAt"]) {
    const inMod = mod.split("\n").filter((l) => l.startsWith(`let ${name} `)).length;
    const inApp = app.split("\n").filter((l) => l.startsWith(`let ${name} `)).length;
    assert.equal(inMod, 1, `${name} must be declared here`);
    assert.equal(inApp, 0, `${name} must not remain in app.js`);
  }
});
