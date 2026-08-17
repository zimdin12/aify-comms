#!/usr/bin/env node
// A test that points a module at a fake service must neutralise EVERY carrier of that value, not one.
//
// THE FAILURE THIS EXISTS FOR, reproduced exactly. A reviewer ran the full bridge gate in a LIVE wrapper
// environment and got `pass 2067, fail 94` where this machine got 2161/0. The cause was not the invocation and
// not product behaviour: the modules read
//
//     process.env.CLAUDE_MCP_SERVER_URL || process.env.AIFY_SERVER_URL
//
// so the LEGACY name wins — and a live claude-aify wrapper exports it. Seventeen test files set only
// `AIFY_SERVER_URL` before importing, so in that environment their fake servers on 127.0.0.2 were never used and
// every request went to the operator's real service, which answered with real 404s ("Agent \"coder\" not
// found") while the fixture server recorded nothing. Setting `CLAUDE_MCP_SERVER_URL` to a decoy on THIS machine
// reproduced `2067/94` to the test, and sealing both names returned it to 2161/0.
//
// So the rule is not "remember the other name". It is that a carrier PAIR in the product must be sealed as a
// pair in the tests, and that has to be checked mechanically, because the failure is invisible in any
// environment where the legacy variable happens to be unset — which is every developer machine that is not
// running a wrapper.
//
// SCOPE: the pairs that select a SERVICE or a CREDENTIAL. TEMP/TMP and HOME/USERPROFILE are also pairs, and
// tests do seal them (see the hermes carrier gate on the Python side), but they are directory carriers rather
// than "which service answers me", and a test that gets them wrong fails loudly on this machine instead of
// silently reaching production.

import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const STDIO = fileURLToPath(new URL("..", import.meta.url));
const TESTS = fileURLToPath(new URL(".", import.meta.url));

// Discovered from the product rather than hardcoded: any `process.env.A || process.env.B` in the bridge whose
// names both look like a service URL or an API key.
function carrierPairsInProduct() {
  const pairs = new Map();
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.name === "node_modules" || entry.name === "tests") continue;
      const full = join(dir, entry.name);
      if (entry.isDirectory()) { walk(full); continue; }
      if (!/\.(js|mjs)$/.test(entry.name)) continue;
      const source = readFileSync(full, "utf-8");
      const re = /process\.env\.([A-Z0-9_]+)\s*\|\|\s*process\.env\.([A-Z0-9_]+)/g;
      let m;
      while ((m = re.exec(source)) !== null) {
        const [a, b] = [m[1], m[2]];
        if (!/(SERVER_URL|API_KEY|FALLBACK_URLS)$/.test(a) || !/(SERVER_URL|API_KEY|FALLBACK_URLS)$/.test(b)) continue;
        const key = [a, b].sort().join("|");
        if (!pairs.has(key)) pairs.set(key, new Set());
        pairs.get(key).add(`${entry.name}`);
      }
    }
  };
  walk(STDIO);
  return pairs;
}

const testFiles = () => readdirSync(TESTS).filter((n) => n.endsWith(".test.js")).sort();

// This file names both members of every pair by construction, and would otherwise report itself.
const SELF = "env-carrier-pairs-are-sealed-together.test.js";

const pairs = carrierPairsInProduct();

assert.ok(pairs.size > 0,
  "no service/credential carrier pairs were found in the bridge — the scanner is broken, not the product");

// The pair that caused the incident must be among them; if it is ever refactored away this assertion is the
// place to learn that, rather than the gate quietly checking nothing.
assert.ok(pairs.has("AIFY_SERVER_URL|CLAUDE_MCP_SERVER_URL"),
  `the server-URL pair is no longer read as a pair: ${[...pairs.keys()].join(", ")}`);

// KEYED ON WRITES, NOT MENTIONS. The first version of this gate flagged five files that merely NAME a carrier:
// fake `/proc` environ data, an extraction-proof plan listing a moved constant, a sentence in a header, an
// assertion about generated TOML text, and an env OBJECT handed to a function. None of them seals anything, and
// a gate that demands they "fix" it teaches people to add noise to silence it. What matters is a test that
// WRITES the real `process.env`, because only that can redirect a module.
function sealsCarrier(source, name) {
  return new RegExp(
    `(?:process\\.env\\.${name}\\s*=)`         // process.env.X = ...
    + `|(?:delete\\s+process\\.env\\.${name})`  // delete process.env.X
    + `|(?:process\\.env\\[["']${name}["']\\]\\s*=)`, // process.env["X"] = ...
  ).test(source);
}

for (const [key, modules] of pairs) {
  const [a, b] = key.split("|");
  const offenders = [];
  for (const name of testFiles()) {
    if (name === SELF) continue;
    const source = readFileSync(join(TESTS, name), "utf-8");
    const sealsA = sealsCarrier(source, a);
    const sealsB = sealsCarrier(source, b);
    if (sealsA !== sealsB) offenders.push(`${name} (seals ${sealsA ? a : b}, not ${sealsA ? b : a})`);
  }
  assert.deepEqual(offenders, [], [
    `${a} and ${b} are ONE value read as a pair (${[...modules].sort().join(", ")}), so a test that seals`,
    "one and not the other is hermetic only where the unsealed name happens to be absent. That is every",
    "developer machine and no wrapper environment — which is how 94 failures reached a reviewer and nobody",
    "else. Set BOTH to the fake, or delete both.",
    "Offenders:",
    ...offenders.map((o) => `  - ${o}`),
  ].join("\n"));
}

console.log(
  `env-carrier-pairs-are-sealed-together.test.js: ${pairs.size} carrier pair(s) checked across `
  + `${testFiles().length} test files — all sealed as pairs`,
);
