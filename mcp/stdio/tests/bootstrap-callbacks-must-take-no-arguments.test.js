#!/usr/bin/env node
// A callback the bootstrap invokes with no arguments must be callable with no arguments.
//
// THE OUTAGE, 2026-08-18. Every wrapper start printed:
//
//     [aify] boot survivor sweep: reaped 7 orphaned managed survivor(s)
//     [aify] environment bridge bootstrap failed: Cannot destructure property 'MACHINE_ID' of
//            'undefined' as it is undefined.
//
// so the environment bridge killed seven managed gateway hosts on its way down and then never came
// up, leaving the environment with no bridge and queued spawns that could not produce a worker.
//
// `bootstrapManagedEnvironmentBridge` calls each of its callbacks with NO arguments.
// `syncManagedAgents` was passed the bare `syncManagedEnvironmentAgents`, which destructures
// `{MACHINE_ID, …}` with no default — so it threw on the first property, and the bootstrap's catch
// fails closed by design, which turned a one-line wiring slip into a dead fleet.
//
// WHY NOTHING CAUGHT IT. The v0.5.4 extraction gave that function injected dependencies and updated
// the direct caller but not this callback. The moved body is byte-identical, so the reconstruction
// gates prove exactly nothing about it: what changed is that the function now REQUIRES arguments.
// An external reviewer named this same blind spot days earlier for `3d4372a4`, where a deleted
// spread-import crashed `aify-comms doctor` for two commits behind a fully green suite.
//
// WHAT THIS ASSERTS, and why it is a real check rather than a source-shaped one: `Function.length`
// is the number of parameters BEFORE the first default or rest — so it is 0 for `f()`, 0 for
// `f({…} = {})`, and 1 for `f({…})`. Reading it off the imported function object asks the runtime
// what the contract is, instead of asking the file what it looks like. A callback wired as a bare
// identifier must therefore report 0.
//
// It cannot be defeated by moving the function, renaming the module, or reformatting the call.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const SERVER = fileURLToPath(new URL("../server.js", import.meta.url));
const source = readFileSync(SERVER, "utf-8");

// The wiring under test: the object literal handed to the bootstrap.
const callMatch = source.match(/bootstrapManagedEnvironmentBridge\(\{([\s\S]*?)\n\s*\}\)/);
assert.ok(callMatch, "could not find the bootstrapManagedEnvironmentBridge({...}) call in server.js");
const literal = callMatch[1];

// `key: identifier,` — a BARE reference. `key: () => …` is a closure and supplies its own arguments,
// which is the fix shape and needs no check.
const bareRefs = [...literal.matchAll(/^\s*(\w+):\s*([A-Za-z_$][\w$]*)\s*,/gm)]
  .map(([, key, ident]) => ({ key, ident }));

// Where each identifier comes from, so the function OBJECT can be inspected rather than its text.
function importSpecifierFor(identifier) {
  const pattern = new RegExp(
    `import\\s*\\{([^}]*)\\}\\s*from\\s*["'](\\.[^"']+)["']`, "g");
  for (const [, names, specifier] of source.matchAll(pattern)) {
    const imported = names.split(",").map((n) => n.trim().split(/\s+as\s+/));
    for (const [original, alias] of imported) {
      if ((alias || original) === identifier) return { specifier, original };
    }
  }
  return null;
}

const checked = [];
const locallyDeclared = [];

for (const { key, ident } of bareRefs) {
  const found = importSpecifierFor(ident);
  if (!found) {
    // Declared inside server.js, which is a bin entry point and cannot be imported. Recorded rather
    // than silently skipped — this is the residual hole, and naming it is how it stays visible.
    locallyDeclared.push(`${key}: ${ident}`);
    continue;
  }
  // Resolved against SERVER.JS, not against this test file: the specifiers are written from
  // server.js's directory and resolving them from tests/ silently looks for siblings that do
  // not exist.
  const mod = await import(new URL(found.specifier, new URL("../server.js", import.meta.url)).href);
  const fn = mod[found.original];
  assert.equal(typeof fn, "function",
    `server.js wires \`${key}: ${ident}\` but ${found.specifier} exports no such function`);
  assert.equal(
    fn.length, 0,
    `\`${key}: ${ident}\` is passed to bootstrapManagedEnvironmentBridge as a BARE REFERENCE, but `
    + `${found.original} requires ${fn.length} argument(s) — the bootstrap calls it with none, so it `
    + `will throw ("Cannot destructure property ... of 'undefined'"), the bootstrap fails closed, and `
    + `the environment bridge dies AFTER reaping its boot survivors. Wrap it in a closure that `
    + `supplies its dependencies, the way \`registerEnvironment\` does.`,
  );
  checked.push(`${key}: ${ident} (arity ${fn.length})`);
}

// ANTI-VACUITY. If the regex stops matching the wiring — reformatted, renamed, moved — this file
// would pass while checking nothing, which is the exact failure mode of the gates that missed the
// original bug.
assert.ok(bareRefs.length + literal.includes("=>") > 0,
  "no callbacks were found in the bootstrap literal at all; the extractor is broken");
assert.ok(checked.length > 0,
  "no imported callback was actually inspected — the import map no longer resolves, so this gate "
  + "is asserting nothing");

console.log(
  `bootstrap-callbacks-must-take-no-arguments: ${checked.length} imported callback(s) verified `
  + `zero-arg [${checked.join(", ")}]`
  + (locallyDeclared.length
    ? `; ${locallyDeclared.length} declared in server.js and not importable: ${locallyDeclared.join(", ")}`
    : ""),
);
