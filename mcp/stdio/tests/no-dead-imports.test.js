// No bridge module may import a name it never uses.
//
// THIS GATE EXISTS BECAUSE THE v0.5.4 DECOMPOSITION MANUFACTURED THEM. Every owner move takes a function out
// of `server.js`; the names that function alone used stay behind in the import block, referenced by nothing.
// Fourteen had accumulated in `server.js` by the time `comms_register` moved out — one of them
// `isClaudeTurnDetectorArmed`, whose disappearance from `server.js` is exactly what broke a test that named
// the file instead of the invariant. A dead import is not merely untidy: it is a false signal about what a
// module depends on, and the next person measuring an extraction's import surface will measure the lie.
//
// It is a real check, not a lint preference — `node --check` and all three suites pass with every one of
// them present.
//
// WHAT COUNTS AS USED is any occurrence of the identifier anywhere else in the file's CODE. Comments and
// module specifiers are stripped first. The specifier matters more than it sounds: without stripping it,
// `import fs from "fs"` can never be reported, because the quoted `"fs"` counts as a second occurrence of
// the identifier — and `fs`, `path` and `os` are the most common default imports in this bridge. The
// synthetic case at the bottom of this file is what found that; the first version of this gate silently
// exempted every one of them.
//
// The bias is otherwise one-directional: a false POSITIVE would fail the suite on working code, so anything
// ambiguous is treated as used.

import assert from "node:assert/strict";
import test from "node:test";

import { bridgeSources } from "./bridge-sources.mjs";

// SWEPT, AND THE CARVE-OUT IS GONE. `hermes-managed-host.js` was exempted here while it carried first 21
// and then 74 dead names, because the reconstruction proof in `hermes-gateway-extraction.test.js`
// byte-compares that file against a pristine fixture: its import-block format is load-bearing, and deleting
// the names breaks the proof unless the proof is updated in the same change. It now is — the proof declares
// the 74 removed names and checks that the surviving region is the pristine one minus exactly those. So the
// exemption was deleted rather than left to rot, which is what the count test below existed to force.
//
// The coupling still holds for the NEXT slice that strands imports in that file: clean them together with
// the proof, or not at all. Sweeping them as a side effect is what cost a whole attempt once.

function strip(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^.*?\/\/.*$/gm, (line) => line.split("//")[0]);
}

// EXPORTED so the sweep that removes these can use the gate's own detector instead of a second copy of
// the rule. The Python side learned this the hard way: a sweep tool carrying its own regex deleted four
// LIVE imports because its copy had drifted from the gate's.
export function deadImportsIn(text) {
  const withSpecifiers = strip(text);
  // Blank the module specifiers before counting uses, but keep them for PARSING the import statements.
  //
  // SPREAD DOTS ARE NOT PROPERTY ACCESS. The count below excludes a name preceded by `.` so that
  // `obj.name` does not look like a use of an imported `name` — but `...name(x)` is a plain call
  // whose preceding character is also a dot, so a helper used ONLY in a spread read as dead. That is
  // not hypothetical: `splitServerUrls` and `defaultFallbackServerUrls` are called exactly that way
  // in `claude-channel.js`, and this gate reported both as dead the moment they were imported rather
  // than declared locally. Deleting them on that advice would have broken the fallback URL set.
  //
  // Blanking the spread operator keeps the member-access exclusion intact and makes the call visible.
  const src = withSpecifiers.replace(/(\bfrom\s*)"[^"]*"/g, '$1""').replace(/\.\.\./g, " ");
  const names = new Set();
  const addNamed = (block) => {
    for (const raw of block.split(",")) {
      const name = raw.trim().split(" as ").pop();
      if (name && /^\w+$/.test(name)) names.add(name);
    }
  };
  // Named block, optionally preceded by a default binding. The `(?:(\w+)\s*,\s*)?` is the form the
  // detector used to be blind to: `import def, { named } from "x"` matched NEITHER this pattern (it
  // required `import` to be followed directly by `{`) nor the default pattern below (it required
  // `from` directly after the name), so BOTH bindings were invisible and a dead one could never be
  // reported. Neither form is used in the bridge today — this is preventive, and the fixtures at the
  // bottom are what prove it works, since a clean tree cannot.
  for (const m of withSpecifiers.matchAll(/^import\s+(?:(\w+)\s*,\s*)?\{([^}]*)\}\s*from\s*"[^"]+";/gm)) {
    if (m[1]) names.add(m[1]);
    addNamed(m[2]);
  }
  for (const m of withSpecifiers.matchAll(/^import\s+(\w+)\s+from\s+"/gm)) names.add(m[1]);
  // Namespace import. `import * as ns from "x"` was never collected at all, so an unused namespace
  // was permanently unreportable. A USED one appears again as `ns.member`, and the member-access
  // exclusion does not apply to the namespace itself — only to what follows the dot — so the
  // occurrence count works unchanged.
  for (const m of withSpecifiers.matchAll(/^import\s*\*\s*as\s+(\w+)\s+from\s+"/gm)) names.add(m[1]);
  return [...names].filter(
    (n) => (src.match(new RegExp(`(?<![\\w$.])${n}(?![\\w])`, "g")) || []).length < 2,
  ).sort();
}

test("no bridge module imports a name it never uses", () => {
  const offenders = bridgeSources()
    .map(([file, text]) => [file, deadImportsIn(text)])
    .filter(([, dead]) => dead.length);
  assert.deepEqual(offenders, [],
    "dead imports: " + offenders.map(([f, d]) => `${f} (${d.join(", ")})`).join("; "));
});

test("hermes-managed-host.js stays clean — the swept file does not re-accumulate", () => {
  // This replaces the carve-out counter. That test pinned the debt so it could not grow silently and so
  // cleaning the file would fail it and force the exemption's deletion; both have now happened. What is
  // worth keeping is the file-specific guard, because this is the file the decomposition kept stranding
  // imports in — five moved functions took 53 names' usages with them in one slice.
  const [, text] = bridgeSources().find(([file]) => file === "hermes-managed-host.js");
  assert.deepEqual(deadImportsIn(text), [],
    "hermes-managed-host.js has dead imports again — sweep them WITH the reconstruction proof, not after it");
});

test("the detector really detects — it finds a dead import in a synthetic module", () => {
  // Anti-vacuity. A scanner with a broken regex would report zero offenders and read as a clean bridge.
  const used = `import { a, b } from "./x.mjs";\nexport const y = a(b);\n`;
  assert.deepEqual(deadImportsIn(used), [], "a module using both imports must be clean");
  const dead = `import { a, b } from "./x.mjs";\nexport const y = a(1);\n`;
  assert.deepEqual(deadImportsIn(dead), ["b"], "…and one that drops `b` must be caught");
  const defaulted = `import fs from "fs";\nexport const y = 1;\n`;
  assert.deepEqual(deadImportsIn(defaulted), ["fs"], "default imports count too");
  // A name mentioned ONLY in a comment is still dead — comments are stripped before counting.
  const commented = `import { a } from "./x.mjs";\n// a is coming back next slice\nexport const y = 1;\n`;
  assert.deepEqual(deadImportsIn(commented), ["a"], "a comment-only mention does not rescue it");
  // Aliased imports are judged on the LOCAL name, which is the one that would be dangling.
  const aliased = `import { spawnSync as sp } from "node:child_process";\nexport const y = sp(1);\n`;
  assert.deepEqual(deadImportsIn(aliased), [], "an aliased import used under its alias is live");

  // SPREAD-CALLED IMPORTS ARE LIVE, and the detector said otherwise until v0.5.4. The count excludes
  // a name preceded by `.` so `obj.name` is not mistaken for a use — but `...name(x)` is a call whose
  // preceding character is also a dot. `splitServerUrls` and `defaultFallbackServerUrls` are used
  // exactly that way in `claude-channel.js`, and both were reported dead the moment they became
  // imports instead of local declarations. Acting on that would have emptied the fallback URL set.
  const spread = `import { a } from "./x.mjs";\nexport const y = [...a("k")];\n`;
  assert.deepEqual(deadImportsIn(spread), [], "a helper used only in a spread is live");
  // …and the member-access exclusion the dot rule exists for still holds.
  const member = `import { a } from "./x.mjs";\nexport const y = obj.a;\n`;
  assert.deepEqual(deadImportsIn(member), ["a"], "`obj.a` is not a use of the imported `a`");
});

test("the two import forms the detector used to be blind to", () => {
  // NEITHER FORM APPEARS IN THE BRIDGE TODAY, which is exactly why these are fixtures. A clean tree
  // cannot tell a fixed detector from the broken one — the old version reported [] for all four
  // cases below and looked just as green as this one does.

  // 1. NAMESPACE IMPORT. Never collected at all, so an unused namespace was permanently
  //    unreportable: no amount of dead `import * as x` would ever fail this gate.
  const namespaceDead = `import * as ns from "./x.mjs";\nexport const y = 1;\n`;
  assert.deepEqual(deadImportsIn(namespaceDead), ["ns"], "an unused namespace import is dead");

  const namespaceUsed = `import * as ns from "./x.mjs";\nexport const y = ns.a();\n`;
  assert.deepEqual(deadImportsIn(namespaceUsed), [], "`ns.a()` is a use of the namespace itself");

  // The member-access exclusion must not swallow the namespace: `ns` in `ns.a` is BEFORE the dot,
  // not after it, so the rule that kills `obj.a` leaves this alone.
  const namespaceOnlyMember = `import * as ns from "./x.mjs";\nexport const y = ns.deep.thing;\n`;
  assert.deepEqual(deadImportsIn(namespaceOnlyMember), [], "a nested member read still uses `ns`");

  // 2. DEFAULT + NAMED COMBINED. Matched neither pattern before — the named pattern wanted `import`
  //    followed directly by `{`, the default pattern wanted `from` directly after the name — so BOTH
  //    bindings vanished and either could rot unreported.
  const combinedDead = `import def, { named } from "./x.mjs";\nexport const y = 1;\n`;
  assert.deepEqual(deadImportsIn(combinedDead), ["def", "named"], "both bindings are reported");

  const combinedPartlyUsed = `import def, { named } from "./x.mjs";\nexport const y = named(1);\n`;
  assert.deepEqual(deadImportsIn(combinedPartlyUsed), ["def"], "only the unused half is reported");

  const combinedUsed = `import def, { named } from "./x.mjs";\nexport const y = def(named);\n`;
  assert.deepEqual(deadImportsIn(combinedUsed), [], "neither is dead when both are called");

  // The plain forms must keep working — widening a regex is how the OTHER cases get lost.
  assert.deepEqual(deadImportsIn(`import { a } from "./x.mjs";\nexport const y = 1;\n`), ["a"]);
  assert.deepEqual(deadImportsIn(`import fs from "node:fs";\nexport const y = 1;\n`), ["fs"]);
  assert.deepEqual(
    deadImportsIn(`import {\n  a,\n  b,\n} from "./x.mjs";\nexport const y = a(b);\n`), [],
    "a multi-line named block still resolves both names",
  );
});
