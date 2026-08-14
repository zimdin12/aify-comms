// The controller boundary: what controllers may import, and that they actually use it.
//
// SIXTH BACKLOG PAYMENT. `runtimes-helpers.js` is a pure re-export hub, so the obvious test — "it exports
// 28 functions" — would be nearly vacuous. Its header states a real invariant instead, and that is what is
// asserted here:
//
//   "Controllers import from THIS file instead of directly from runtimes.js as a forward-compatible
//    boundary … each new controller can add helpers here without touching runtimes.js itself."
//
// A boundary nothing enforces is a convention, and conventions decay one hurried import at a time. Nine
// controllers go through it today and none bypasses it; this makes the tenth fail loudly instead.
//
// IMPORTING THE HUB IS ITSELF A CHECK. ESM resolves re-exports at load, so a name removed or renamed in
// runtimes.js makes this file throw on import rather than at whatever call site reaches it first. That is
// why the surface assertion below is worth having at all despite looking thin.

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import * as helpers from "../runtimes-helpers.js";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CONTROLLERS = path.join(STDIO, "controllers");

const controllerFiles = () =>
  readdirSync(CONTROLLERS).filter((f) => /\.(js|mjs)$/.test(f) && !/\.test\./.test(f));

test("every re-exported name RESOLVES — the import itself proves the boundary is intact", () => {
  // If runtimes.js dropped or renamed one of these, this module would throw at import and every
  // controller with it. Asserting they are all callable turns that into a named failure here rather than
  // a cryptic one at bridge start.
  const names = Object.keys(helpers);
  assert.ok(names.length >= 25, `expected the full helper surface, found ${names.length}`);
  const notFunctions = names.filter((n) => typeof helpers[n] !== "function");
  assert.deepEqual(notFunctions, [], `these re-exports are not callable: ${notFunctions.join(", ")}`);
});

test("the hub re-exports and declares NOTHING of its own", () => {
  // Its whole value is being a boundary. A helper body implemented here would be a second home for
  // behaviour that lives in runtimes.js, and the two would drift.
  const src = readFileSync(path.join(STDIO, "runtimes-helpers.js"), "utf-8");
  assert.doesNotMatch(src, /^\s*(?:export\s+)?(?:async\s+)?function\s/m, "no function may be defined here");
  assert.doesNotMatch(src, /^\s*(?:export\s+)?(?:const|let|var)\s+\w+\s*=/m, "no binding may be defined here");
  assert.match(src, /^\}\s*from\s+"\.\/runtimes\.js";$/m, "everything comes from one place");
});

test("NO CONTROLLER IMPORTS runtimes.js DIRECTLY — the boundary is used, not just offered", () => {
  // The invariant the module header claims. Nine controllers respect it today; this is what makes the
  // tenth fail loudly rather than quietly re-coupling the layer.
  const offenders = [];
  for (const file of controllerFiles()) {
    const src = readFileSync(path.join(CONTROLLERS, file), "utf-8");
    if (/from\s+["']\.\.\/runtimes\.js["']/.test(src)) offenders.push(file);
  }
  assert.deepEqual(
    offenders, [],
    "these controllers bypass runtimes-helpers.js and import runtimes.js directly:\n  " + offenders.join("\n  ")
      + "\nAdd what you need to runtimes-helpers.js instead — that is what the boundary is for.",
  );
});

test("the scan actually reaches the controllers, and they DO use the boundary", () => {
  // Anti-vacuity for the test above: an empty directory listing, or a rename of controllers/, would make
  // "no offenders" true for the wrong reason. Both halves are checked — the files exist, and a real
  // majority of them import the hub.
  const files = controllerFiles();
  assert.ok(files.length >= 5, `expected several controllers, found ${files.length}`);

  const users = files.filter((f) =>
    /runtimes-helpers/.test(readFileSync(path.join(CONTROLLERS, f), "utf-8")));
  assert.ok(users.length >= 5,
    `expected most controllers to import the boundary, only ${users.length} of ${files.length} do`);
});

test("the helpers the controllers actually name are all present", () => {
  // Reads the import lists out of the controllers and checks each name against the hub.
  //
  // ESM USUALLY CATCHES THIS FIRST, and harder — verified by planting `import { noSuchHelper }` in a
  // controller: `runtimes.js` transitively loads the controllers, so the whole suite failed at load with
  // "does not provide an export named 'noSuchHelper'" before this assertion ran. This is not the primary
  // guard, then; it is the one that names the FILE and the HELPER instead of leaving a bare SyntaxError,
  // and it still covers a controller that nothing happens to load.
  const missing = new Set();
  for (const file of controllerFiles()) {
    const src = readFileSync(path.join(CONTROLLERS, file), "utf-8");
    for (const m of src.matchAll(/import\s*\{([^}]*)\}\s*from\s*["'][^"']*runtimes-helpers\.js["']/g)) {
      for (const raw of m[1].replace(/\/\/[^\n]*/g, "").split(",")) {
        const name = raw.trim().split(/\s+as\s+/)[0].trim();
        if (name && !(name in helpers)) missing.add(`${file}: ${name}`);
      }
    }
  }
  assert.deepEqual([...missing], [], "controllers import these names, but the hub does not provide them");
});
