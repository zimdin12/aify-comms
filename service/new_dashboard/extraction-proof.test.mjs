// Proves the settings-fields extraction was a PURE FILE SPLIT, and proves the prover can fail.
//
// The extracted module's own tests show the moved code works. They cannot show that nothing ELSE in a
// 5,000-line file changed — a whitespace edit two functions away, a line dropped during the splice, an
// import inserted in the wrong place. So this reconstructs the pre-slice `app.js` from the post-slice
// `app.js` plus the extracted module, and requires the result to be byte-identical to the recorded
// pre-slice text.
//
// The pre-slice source is a TRACKED FIXTURE rather than a `git show`. A proof that needs `.git` does not
// run from `git archive`, and that exact mistake shipped a route-surface gate in v0.5 that had never been
// in the repo at all: `.gitignore`'s bare `data/` matched `service/tests/data/`, the snapshots were
// untracked, and the gate raised FileNotFoundError on a clean clone while passing locally.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

import { functionSpan, moduleScopeBrowserRefs, reconstruct } from "./extraction-proof.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const read = (p) => fs.readFileSync(path.join(HERE, p), "utf-8");

const PLAN = {
  names: ["settingsFieldHtml", "themePreviewTilesHtml"],
  importLine: "import { settingsFieldHtml } from './settings-fields.mjs';",
  marker: [
    "// settingsFieldHtml moved to ./settings-fields.mjs in v0.5.4 (with themePreviewTilesHtml, which",
    "// only it calls and which stays private there).",
  ],
  // themePreviewTilesHtml was removed leaving no marker; it is restored at its original line index.
  reinsert: { themePreviewTilesHtml: 1041 },
};

test("app.js reconstructs byte-identically from the split plus the extracted module", () => {
  const rebuilt = reconstruct({
    after: read("app.js"),
    module: read("settings-fields.mjs"),
    plan: PLAN,
  });
  const expected = read("fixtures/app.before-settings-fields.js");
  assert.equal(
    rebuilt,
    expected,
    "reconstruction differs from the pre-slice app.js, so the split changed something outside the "
      + "extracted spans",
  );
});

test("the reconstruction fixture is TRACKED, not ignored", () => {
  // Guards the v0.5 failure directly: a fixture matched by .gitignore passes here and does not exist in
  // the candidate tree.
  const rel = "service/new_dashboard/fixtures/app.before-settings-fields.js";
  const out = execFileSyncSafe("git", ["check-ignore", rel]);
  assert.equal(out.ignored, false, `${rel} is git-ignored, so this proof would not exist on a clean clone`);
});

function execFileSyncSafe(cmd, args) {
  // `git check-ignore` exits 1 when the path is NOT ignored, which is the success case here. Written with
  // a static import because this file is ESM and `require` is not defined in it — my first version used
  // `require` and the test failed on the harness rather than on the property.
  try {
    execFileSync(cmd, args, { cwd: path.join(HERE, "..", ".."), stdio: "pipe" });
    return { ignored: true };
  } catch {
    return { ignored: false };
  }
}

test("the extracted module has NO module-scope browser globals", () => {
  const hits = moduleScopeBrowserRefs(read("settings-fields.mjs"));
  assert.deepEqual(
    hits,
    [],
    "an extracted module with module-scope browser code is as unimportable as app.js, which defeats the "
      + `point of extracting it: ${JSON.stringify(hits)}`,
  );
});

test("the purity check can actually SEE a module-scope browser global", () => {
  // Without this, the assertion above passes by matching nothing.
  const hits = moduleScopeBrowserRefs("const byId = (id) => document.getElementById(id);\n");
  assert.equal(hits.length, 1);
  assert.equal(hits[0].global, "document");
});

test("the purity check ignores browser globals INSIDE a function body", () => {
  // A function that touches the DOM when CALLED is fine; only module scope runs on import.
  const hits = moduleScopeBrowserRefs("function f() {\n  return document.title;\n}\n");
  assert.deepEqual(hits, []);
});

test("reconstruction REFUSES when the wrong function is substituted", () => {
  // Swapping the order changes which body lands at the marker and which is re-inserted, and the plan no
  // longer has an index for the one that moved. It throws rather than returning a merely-different string,
  // which is the better failure: a proof that cannot map its inputs must stop, not disagree.
  const swapped = { ...PLAN, names: ["themePreviewTilesHtml", "settingsFieldHtml"] };
  assert.throws(
    () => reconstruct({ after: read("app.js"), module: read("settings-fields.mjs"), plan: swapped }),
    /no reinsert index recorded for settingsFieldHtml/,
  );
});

test("reconstruction FAILS when whitespace outside the extracted spans moves", () => {
  const original = read("app.js");
  // VERIFY THE TAMPER LANDED before reading the result. My first version replaced a string that does not
  // occur in app.js, so it tampered with nothing and the test passed while proving nothing — the same
  // class as a mutation applied to a docstring instead of to code.
  const needle = "const SETTINGS_SCHEMA = [";
  assert.ok(original.includes(needle), "the tamper target must exist in app.js");
  const tampered = original.replace(needle, "const  SETTINGS_SCHEMA = [");
  assert.notEqual(tampered, original, "the tamper must actually change the source");

  const rebuilt = reconstruct({ after: tampered, module: read("settings-fields.mjs"), plan: PLAN });
  assert.notEqual(
    rebuilt,
    read("fixtures/app.before-settings-fields.js"),
    "an edit outside the extracted spans must break reconstruction",
  );
});

test("reconstruction FAILS when the marker comment does not match verbatim", () => {
  const tampered = read("app.js").replace(PLAN.marker[1], "// only it calls.");
  assert.throws(
    () => reconstruct({ after: tampered, module: read("settings-fields.mjs"), plan: PLAN }),
    /marker comment line 1 does not match/,
    "a loosened marker mask could hide an edit, so a changed marker must throw rather than adapt",
  );
});

test("reconstruction FAILS when the import line is absent", () => {
  const tampered = read("app.js").replace(PLAN.importLine, "import { settingsFieldHtml } from './x.mjs';");
  assert.throws(
    () => reconstruct({ after: tampered, module: read("settings-fields.mjs"), plan: PLAN }),
    /import line not found verbatim/,
  );
});

test("functionSpan finds a whole brace-matched body, not the first closing brace", () => {
  const src = "function outer(a) {\n  if (a) {\n    return 1;\n  }\n  return 2;\n}\nfunction after() {}\n";
  const span = functionSpan(src, "outer");
  assert.match(span.text, /return 2;/, "the span must run to the function's own closing brace");
  assert.doesNotMatch(span.text, /function after/);
});
