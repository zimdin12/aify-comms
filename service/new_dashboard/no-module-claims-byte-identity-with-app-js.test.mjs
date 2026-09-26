// No dashboard module claims its code is byte-identical to what once stood in app.js (0.7.1 C8).
//
// The v0.5.4 extractions each carried that claim, and a reconstruction proof held it true. The proof
// was retired in v0.7 and several of those modules were edited in 0.7, so nineteen headers stated
// something no longer true and nothing checked. A reader trusting one would skip reading the code
// they most needed to read. The population is every non-test module in the directory listing.

import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DIR = path.dirname(fileURLToPath(import.meta.url));

// A claim about the code as it stands. "passed the byte-identical reconstruction proof" is history
// about an earlier slice, and stays.
const CLAIM = /byte-identical(?! reconstruction proof)/i;

function modules() {
  return fs.readdirSync(DIR)
    .filter((name) => /\.(mjs|js)$/.test(name) && !name.includes(".test."))
    .map((name) => [name, fs.readFileSync(path.join(DIR, name), "utf8")]);
}

test("no dashboard module claims byte-identity with code that stood elsewhere", () => {
  const all = modules();
  assert.ok(all.length >= 40, `only ${all.length} modules found; the scan is broken`);
  const claims = all.flatMap(([name, source]) => source.split("\n")
    .map((line, i) => [i + 1, line])
    .filter(([, line]) => CLAIM.test(line))
    .map(([n, line]) => `${name}:${n}: ${line.trim()}`));
  assert.deepEqual(claims, [], `the proof behind these is retired:\n  ${claims.join("\n  ")}`);
});

test("CONTROL: the pattern finds the claim and passes the history", () => {
  assert.ok(CLAIM.test("// The declarations are byte-identical to those that stood in app.js"));
  assert.ok(!CLAIM.test("// it was written, passed the byte-identical reconstruction proof, and had to be reverted"));
});
