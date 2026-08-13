// Dedupe keeping the FIRST occurrence — and the falsy-dropping that is easy to read as incidental.
//
// Two unrelated callers depend on both properties. `cwdRootsForEnvironment` builds the workspace roots an
// environment advertises, where the first root is the default a spawn lands in; message fan-out builds a
// recipient list reported back to the sender in the order it was addressed. Order is part of the answer in
// both, so `[...new Set(values)]` would be right only by accident of insertion order and a sort would be
// wrong.

import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { dedupePreserveOrder } from "../dedupe.mjs";
import { declaringModules, isUsedInBridge } from "./bridge-sources.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("FIRST OCCURRENCE WINS and the order is the input's, not sorted", () => {
  // The fixture is deliberately not already sorted: with `a b c` a `.sort()` bug is invisible.
  assert.deepEqual(dedupePreserveOrder(["c", "a", "c", "b", "a"]), ["c", "a", "b"]);
  assert.deepEqual(dedupePreserveOrder(["z", "y"]), ["z", "y"], "no reordering when there is nothing to drop");
  assert.deepEqual(dedupePreserveOrder(["only"]), ["only"]);
});

test("FALSY VALUES ARE DROPPED, not just deduped — and that is the contract, not a side effect", () => {
  // Both callers build their input by splitting and trimming strings, so an empty entry is the residue of a
  // trailing delimiter or a blank env var, never a meaningful member. A `""` cwd root would advertise a
  // workspace nothing can match; a `""` recipient would be an address.
  assert.deepEqual(dedupePreserveOrder(["a", "", "b", "", "a"]), ["a", "b"]);
  assert.deepEqual(dedupePreserveOrder(["", null, undefined, 0, false, NaN]), [],
    "every falsy shape is dropped, so an all-blank list yields nothing at all");
  // The consequence, stated rather than hidden: this CAN return empty. `cwdRootsForEnvironment` inherits it
  // — see the gap pinned in environment-identity.test.js.
  assert.deepEqual(dedupePreserveOrder([""]), []);
  // Truthy values that merely look empty are KEPT: "0" and " " are strings a caller chose to pass.
  assert.deepEqual(dedupePreserveOrder(["0", " ", "0"]), ["0", " "]);
});

test("a missing or empty list is not an error", () => {
  // Callers pass the result of a split that may have matched nothing, and one passes a value that can be
  // undefined. Throwing here would fail an environment heartbeat over an unset variable.
  for (const input of [undefined, null, []]) {
    assert.deepEqual(dedupePreserveOrder(input), [], `${JSON.stringify(input)} must yield an empty list`);
  }
});

test("it does not mutate its input", () => {
  // It is handed arrays built by callers that use them afterwards.
  const input = ["b", "a", "b"];
  const copy = [...input];
  dedupePreserveOrder(input);
  assert.deepEqual(input, copy, "the caller's array must be untouched");
});

test("identity is by value for strings, so equal paths collapse", () => {
  // `Set` membership is SameValueZero. Worth pinning because the callers pass strings that were produced by
  // separate `.trim()` calls — different objects would not collapse, equal strings must.
  const a = "/root/x";
  // Built a different way on purpose, so this is not comparing a string to itself. My first version used
  // `["/root", "/x"].join("/")`, which is `/root//x` — a double slash, and not the same path at all.
  const b = ["", "root", "x"].join("/");
  assert.equal(a, b, "the two strings must really be equal by value, or this test proves nothing");
  assert.deepEqual(dedupePreserveOrder([a, b]), ["/root/x"], "…so the duplicate collapses");
  // And two paths that only LOOK alike must not collapse — `/root//x` is a different string.
  assert.deepEqual(dedupePreserveOrder(["/root/x", "/root//x"]), ["/root/x", "/root//x"],
    "this dedupes strings, it does not normalise paths");
});

test("exactly one module declares it, and the bridge still uses it", () => {
  assert.deepEqual(declaringModules("dedupePreserveOrder"), [{ file: "dedupe.mjs", kind: "function" }],
    "two copies would let the two callers disagree about what an empty entry means");
  assert.ok(isUsedInBridge("dedupePreserveOrder"), "an unused utility is dead code, not a leaf");
});

test("the owner is a pure leaf", () => {
  const src = fs.readFileSync(path.join(STDIO, "dedupe.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state");
  assert.deepEqual([...src.matchAll(/^import .* from "([^"]+)";$/gm)].map((m) => m[1]), [],
    "it must import nothing at all");
});
