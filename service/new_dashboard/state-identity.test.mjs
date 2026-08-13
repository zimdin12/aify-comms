// EXACTLY ONE MODULE MAY DECLARE `state`, and every reader must get that object by identity.
//
// This is the gate that makes the v0.5.4 `state` move safe, and it is the JS analogue of the Python
// `test_process_global_identity.py` in this same series — which was not written speculatively either: it
// caught a real fork, where a second copy of `_listen_events`' module-level queue would have made
// `comms_listen` hang with no error anywhere.
//
// THE FAILURE THIS PREVENTS IS SILENT. `state` is mutated by 26 functions in `app.js`. If a second
// declaration ever appears — a slice that copies the object instead of importing it, a module that
// declares its own `state` for convenience — nothing raises. The dashboard renders from one object while
// the poll loop and the WebSocket handler update another, and the symptom is panels that never change.
// There is no DOM-level test for this dashboard to catch that, so it has to be caught structurally.
//
// The identity half is not a tautology worth skipping: it pins the EXPORT SHAPE. `state` is exported as a
// live binding to one object; re-exporting a copy, or turning it into a getter that builds a fresh object,
// would pass every other test in this directory and break the whole dashboard.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { state } from "./state.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));

// A module-scope declaration is one at column zero. Indented `const state =` inside a function is a local
// and is none of this gate's business — that distinction is why this matches on the line start rather than
// searching the file for the word.
const DECLARES_STATE = /^(?:export\s+)?(?:const|let|var)\s+state\s*=/m;

function sources() {
  return fs
    .readdirSync(HERE)
    .filter((f) => (f.endsWith(".js") || f.endsWith(".mjs")) && !f.endsWith(".test.mjs"))
    .map((f) => [f, fs.readFileSync(path.join(HERE, f), "utf-8")]);
}

test("exactly one dashboard module declares `state` at module scope", () => {
  const declarers = sources().filter(([, text]) => DECLARES_STATE.test(text)).map(([f]) => f);
  assert.deepEqual(declarers, ["state.mjs"],
    "the dashboard's state object must have exactly one owner — a second declaration does not raise, it "
    + "silently splits the dashboard's state in two");
});

test("app.js reads the shared object rather than declaring its own", () => {
  const app = fs.readFileSync(path.join(HERE, "app.js"), "utf-8");
  assert.ok(app.includes("import { state } from './state.mjs';"),
    "app.js must import the shared state object");
  assert.equal(DECLARES_STATE.test(app), false, "app.js must not declare `state` any more");
});

test("every importer gets the SAME object, and mutations are visible across imports", async () => {
  const again = await import("./state.mjs");
  assert.equal(again.state, state, "a second import must yield the same object, not a copy");

  // The property the 26 mutating functions depend on: a write through one reference is a write through
  // all of them. Done on a scratch key and removed afterwards, so this test seals its own effect — a
  // shared singleton is exactly the kind of thing that leaks between tests.
  assert.equal("__identityProbe" in state, false, "the probe key must not already exist");
  try {
    again.state.__identityProbe = 1;
    assert.equal(state.__identityProbe, 1, "a mutation through one import must be visible through another");
  } finally {
    delete again.state.__identityProbe;
  }
  assert.equal("__identityProbe" in state, false, "the probe key must be cleaned up");
});

test("the scanner really detects a declaration — it is not matching nothing", () => {
  // Anti-vacuity. A regex that matched nothing would report exactly one declarer forever, including on the
  // day a second one appeared. Three gates in this series have failed this way.
  assert.ok(DECLARES_STATE.test("const state = {};\n"), "a bare declaration must match");
  assert.ok(DECLARES_STATE.test("export const state = {\n};\n"), "an exported one must match");
  assert.ok(DECLARES_STATE.test("let x = 1;\nvar state = {};\n"), "…on any line, not just the first");
  assert.equal(DECLARES_STATE.test("function f() {\n  const state = {};\n}\n"), false,
    "an indented local must NOT match — it is not module scope");
  assert.equal(DECLARES_STATE.test("const stateful = {};\n"), false, "a longer name must not match");
});
