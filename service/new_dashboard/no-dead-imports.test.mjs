// No dashboard module may import a name it never uses.
//
// `mcp/stdio/tests/no-dead-imports.test.js` has policed exactly this for the bridge since the v0.5.4
// decomposition started manufacturing dead imports — "every owner move takes a function out of
// `server.js`; the names that function alone used stay behind in the import block". The dashboard was
// decomposed the same way, into 59 modules, and nothing asked it the question: that gate's
// `bridgeSources()` reads `mcp/stdio` only.
//
// THE DETECTOR IS IMPORTED, NOT COPIED. `deadImportsIn` is exported for exactly this, and its own
// comment says why: "The Python side learned this the hard way: a sweep tool carrying its own regex
// deleted four LIVE imports because its copy had drifted from the gate's." One rule, two populations.
//
// Pointing it here is what exposed a blind spot in the rule itself. Every pattern in it assumed
// DOUBLE-quoted specifiers; the dashboard writes `from './util.js'`. Un-widened, it collected no
// names from any dashboard module and reported all 59 clean — and the same blindness was hiding nine
// dead imports in `server.js`, which writes some of its own imports single-quoted. A gate that reads
// nothing reports the same green as a gate that found nothing.

import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { deadImportsIn } from "../../mcp/stdio/tests/dead-imports.mjs";

const DIR = path.dirname(fileURLToPath(import.meta.url));

//: app.js is measured like every other module. Its 183 dead imports were frozen by the
//: reconstruction proof, which spliced its import lines verbatim; they went when it was retired.

function dashboardSources() {
  return fs
    .readdirSync(DIR)
    .filter((name) => /\.(mjs|js)$/.test(name) && !name.includes(".test."))
    .filter((name) => fs.statSync(path.join(DIR, name)).isFile())
    .map((name) => [name, fs.readFileSync(path.join(DIR, name), "utf-8")]);
}

test("the dashboard population is real", () => {
  // A directory read that silently returned nothing would make every assertion below vacuous.
  const sources = dashboardSources();
  assert.ok(sources.length >= 40, `only ${sources.length} dashboard modules found`);
  assert.ok(sources.some(([n]) => n === "app.js"), "app.js missing from the scan");
});

test("no dashboard module imports a name it never uses", () => {
  const offenders = dashboardSources()
    .map(([name, text]) => [name, deadImportsIn(text)])
    .filter(([, dead]) => dead.length);
  assert.deepEqual(
    offenders,
    [],
    "dead imports: " + offenders.map(([f, d]) => `${f} (${d.join(", ")})`).join("; "),
  );
});

test("the detector reads THIS directory's quote style", () => {
  // Anti-vacuity, and the specific failure that made this file necessary. The dashboard writes
  // single-quoted specifiers; the detector assumed double. Without this, a broken parse reports a
  // clean dashboard forever.
  const single = `import { alpha, beta } from './x.js';\nexport const y = alpha(1);\n`;
  assert.deepEqual(deadImportsIn(single), ["beta"], "single-quoted imports must be parsed");
  const double = `import { alpha, beta } from "./x.js";\nexport const y = alpha(1);\n`;
  assert.deepEqual(deadImportsIn(double), ["beta"], "double-quoted imports must still be parsed");
  const clean = `import { alpha } from './x.js';\nexport const y = alpha(1);\n`;
  assert.deepEqual(deadImportsIn(clean), [], "a module using its import must be clean");
});

test("a name mentioned only in a moved-to comment is still dead", () => {
  // The v0.5.4 shape exactly: the declaration left, a `// x moved to ./y.mjs` marker stayed, and the
  // import stayed with it. Comments are stripped before counting, so the marker does not save it.
  const moved = `import { gone } from './y.mjs';\n// gone moved to ./y.mjs in v0.5.4.\nexport const z = 1;\n`;
  assert.deepEqual(deadImportsIn(moved), ["gone"]);
});
