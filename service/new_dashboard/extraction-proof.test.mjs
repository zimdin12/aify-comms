// Proves EVERY app.js extraction so far was a pure file split, and proves the prover can fail.
//
// The extracted modules' own tests show the moved code works. They cannot show that nothing ELSE in a
// 5,000-line file changed — a whitespace edit two functions away, a line dropped during a splice, an
// import inserted in the wrong place. So this reconstructs app.js as it was before ANY extraction, from
// the current app.js plus every module extracted since, and requires byte-identity.
//
// ONE PRISTINE FIXTURE, A GROWING PLAN. The first version compared against a per-slice snapshot and went
// stale the moment slice 2 touched app.js — a proof that can only run once is a receipt, not a gate. The
// fixture below never changes; each slice appends an entry to EXTRACTIONS. So this keeps proving the whole
// history, and a later slice cannot quietly undo an earlier one.
//
// The fixture is TRACKED, not a `git show`. A proof that needs `.git` does not run from `git archive`, and
// that exact mistake shipped a route-surface gate in v0.5 that had never been in the repo at all:
// `.gitignore`'s bare `data/` matched `service/tests/data/`, the snapshots were untracked, and the gate
// raised FileNotFoundError on a clean clone while passing locally.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

import { functionSpan, moduleScopeBrowserRefs, reconstruct } from "./extraction-proof.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const read = (p) => fs.readFileSync(path.join(HERE, p), "utf-8");

const LF = String.fromCharCode(10);

const PRISTINE = "fixtures/app.before-settings-fields.js";

/** One entry per extraction slice, in order. `at` values are indices into the PRISTINE file. */
const EXTRACTIONS = [
  {
    module: "settings-fields.mjs",
    importLine: "import { settingsFieldHtml } from './settings-fields.mjs';",
    items: [
      {
        name: "settingsFieldHtml",
        at: 1068,
        marker: [
          "// settingsFieldHtml moved to ./settings-fields.mjs in v0.5.4 (with themePreviewTilesHtml, which",
          "// only it calls and which stays private there).",
        ],
      },
      { name: "themePreviewTilesHtml", at: 1041, marker: null },
    ],
  },
  {
    module: "util.js",
    // This slice EDITED an existing import rather than adding a line, so the proof restores the old text.
    importLine:
      "import { esc, fileSizeLabel, relTime, tsMs, usageFmtTokens, usageResetLabel } from './util.js';",
    importWas: "import { esc, relTime, tsMs } from './util.js';",
    items: [
      // Indices are 0-based positions in the PRISTINE fixture, MEASURED from it rather than copied from
      // the extraction script's output — my first values came from the post-slice-1 file and were wrong by
      // one and by forty-six. The proof caught it, which is the point of it being position-sensitive.
      { name: "fileSizeLabel", at: 304, marker: "// fileSizeLabel moved to ./util.js in v0.5.4." },
      { name: "usageResetLabel", at: 1248, marker: "// usageResetLabel moved to ./util.js in v0.5.4." },
      { name: "usageFmtTokens", at: 1256, marker: "// usageFmtTokens moved to ./util.js in v0.5.4." },
    ],
  },
  {
    module: "record-fields.mjs",
    importLine: "} from './record-fields.mjs';",
    importBlock: [
      "import {",
      "  asAgentArray,",
      "  contractCategory,",
      "  environmentRoots,",
      "  environmentRuntimes,",
      "  messageId,",
      "  messageIdOf,",
      "  messageRunId,",
      "  runPendingControlCount,",
      "  runTargetAgent,",
      "  sessionAgentId,",
      "  sessionEnvironmentId,",
      "  sessionId,",
      "  sessionRuntime,",
      "} from './record-fields.mjs';",
    ],
    items: [
      { name: "messageIdOf", at: 219, marker: "// messageIdOf moved to ./record-fields.mjs in v0.5.4." },
      { name: "asAgentArray", at: 733, marker: "// asAgentArray moved to ./record-fields.mjs in v0.5.4." },
      { name: "sessionEnvironmentId", at: 1617, marker: "// sessionEnvironmentId moved to ./record-fields.mjs in v0.5.4." },
      { name: "sessionRuntime", at: 1621, marker: "// sessionRuntime moved to ./record-fields.mjs in v0.5.4." },
      { name: "messageId", at: 1696, marker: "// messageId moved to ./record-fields.mjs in v0.5.4." },
      { name: "messageRunId", at: 1700, marker: "// messageRunId moved to ./record-fields.mjs in v0.5.4." },
      { name: "contractCategory", at: 2910, marker: "// contractCategory moved to ./record-fields.mjs in v0.5.4." },
      { name: "environmentRoots", at: 2990, marker: "// environmentRoots moved to ./record-fields.mjs in v0.5.4." },
      { name: "runPendingControlCount", at: 3267, marker: "// runPendingControlCount moved to ./record-fields.mjs in v0.5.4." },
      // The last three readers of this shape. Indices MEASURED from the pristine fixture, not copied from
      // the current file — `at` is a position in the pre-extraction app.js, and every earlier slice has
      // already shifted the live one.
      { name: "sessionId", at: 1609, marker: "// sessionId moved to ./record-fields.mjs in v0.5.4." },
      { name: "sessionAgentId", at: 1613, marker: "// sessionAgentId moved to ./record-fields.mjs in v0.5.4." },
      { name: "runTargetAgent", at: 1704, marker: "// runTargetAgent moved to ./record-fields.mjs in v0.5.4." },
      { name: "environmentRuntimes", at: 2983, marker: "// environmentRuntimes moved to ./record-fields.mjs in v0.5.4." },
    ],
  },
  {
    // status.js ALREADY EXISTED and was already imported, so this slice WIDENS an import rather than
    // adding one. `importWas` is what the line looked like before; reconstruct() restores it instead of
    // deleting the line, which is the difference between proving a widening and proving an insertion.
    module: "status.js",
    // ONE LINE, not a block, and that is load-bearing: reconstruct() locates an import block by
    // `indexOf(block[0])`, so a second `import {` opener in app.js would make the record-fields block
    // resolve to this one instead. The pristine file had this import on a single line; keeping it that
    // way keeps the opener unique rather than teaching the harness to disambiguate mid-slice.
    importLine: "import { AGENT_STATUSES, LIVE_AGENT_STATUSES, STATUS_KINDS, renderStatusChip, resolveStatus, runStatusContext, statusWhyContext } from './status.js';",
    importWas: "import { STATUS_KINDS, AGENT_STATUSES, LIVE_AGENT_STATUSES, resolveStatus, renderStatusChip } from './status.js';",
    items: [
      { name: "statusWhyContext", at: 438, marker: "// statusWhyContext moved to ./status.js in v0.5.4." },
      { name: "runStatusContext", at: 3243, marker: "// runStatusContext moved to ./status.js in v0.5.4." },
    ],
  },
  {
    // A NEW module, so `importWas` is absent: reconstruct() deletes the import line rather than
    // restoring a previous one. The line sits immediately after the record-fields block, which is
    // where the extraction put it.
    module: "environment-start-command.mjs",
    importLine: "import { environmentStartCommand } from './environment-start-command.mjs';",
    items: [
      { name: "environmentStartCommand", at: 3106,
        marker: "// environmentStartCommand moved to ./environment-start-command.mjs in v0.5.4." },
    ],
  },
  {
    module: "run-event.mjs",
    importLine: "import { renderRunEvent } from './run-event.mjs';",
    items: [
      { name: "renderEventBody", at: 3271, marker: "// renderEventBody moved to ./run-event.mjs in v0.5.4." },
      { name: "renderRunEvent", at: 3280, marker: "// renderRunEvent moved to ./run-event.mjs in v0.5.4." },
    ],
  },
];

const MODULES = () => ({
  "settings-fields.mjs": read("settings-fields.mjs"),
  "util.js": read("util.js"),
  "record-fields.mjs": read("record-fields.mjs"),
  "status.js": read("status.js"),
  "environment-start-command.mjs": read("environment-start-command.mjs"),
  "run-event.mjs": read("run-event.mjs"),
});

function rebuild(overrides = {}) {
  return reconstruct({
    after: overrides.after ?? read("app.js"),
    modules: overrides.modules ?? MODULES(),
    extractions: overrides.extractions ?? EXTRACTIONS,
  });
}

test("app.js reconstructs byte-identically from every extraction to date", () => {
  assert.equal(
    rebuild(),
    read(PRISTINE),
    "reconstruction differs from the pre-extraction app.js, so some slice changed something outside the "
      + "spans it declared",
  );
});

test("the second slice's marker comment is missing from app.js for one of its items", () => {
  // A guard on the plan itself: the marker text is asserted verbatim by reconstruct(), so if a slice's
  // marker were mistyped here the proof would throw rather than silently skip that body.
  const source = read("app.js");
  for (const step of EXTRACTIONS) {
    for (const item of step.items) {
      if (item.marker == null) continue;
      assert.ok(
        source.includes([].concat(item.marker)[0]),
        `${item.name}'s marker is not in app.js verbatim, so the plan and the file disagree`,
      );
    }
  }
});

test("the reconstruction fixture is TRACKED, not ignored", () => {
  const rel = `service/new_dashboard/${PRISTINE}`;
  assert.equal(
    isGitIgnored(rel),
    false,
    `${rel} is git-ignored, so this proof would not exist on a clean clone`,
  );
});

function isGitIgnored(rel) {
  // `git check-ignore` exits 1 when the path is NOT ignored, which is the success case here. Written with
  // a static import because this file is ESM and `require` is not defined in it — my first version used
  // `require` and the test failed on the harness rather than on the property.
  try {
    execFileSync("git", ["check-ignore", rel], { cwd: path.join(HERE, "..", ".."), stdio: "pipe" });
    return true;
  } catch {
    return false;
  }
}

test("every extracted module has NO module-scope browser globals", () => {
  for (const [name, source] of Object.entries(MODULES())) {
    assert.deepEqual(
      moduleScopeBrowserRefs(source),
      [],
      `${name} has module-scope browser code, which makes it as unimportable as app.js and defeats the `
        + "point of extracting into it",
    );
  }
});

test("the purity check can actually SEE a module-scope browser global", () => {
  // Without this, the assertion above passes by matching nothing.
  const hits = moduleScopeBrowserRefs("const byId = (id) => document.getElementById(id);\n");
  assert.equal(hits.length, 1);
  assert.equal(hits[0].global, "document");
});

test("the purity check ignores browser globals INSIDE a function body", () => {
  // A function that touches the DOM when CALLED is fine; only module scope runs on import.
  assert.deepEqual(moduleScopeBrowserRefs("function f() {\n  return document.title;\n}\n"), []);
});

test("reconstruction FAILS when a body is restored at the wrong line", () => {
  const shifted = EXTRACTIONS.map((step) => ({
    ...step,
    items: step.items.map((item, i) => (i === 0 ? { ...item, at: item.at + 1 } : item)),
  }));
  assert.notEqual(
    rebuild({ extractions: shifted }),
    read(PRISTINE),
    "an off-by-one in a restore index must break reconstruction, or the proof is not position-sensitive",
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
  assert.notEqual(rebuild({ after: tampered }), read(PRISTINE));
});

test("reconstruction REFUSES when a marker comment does not match verbatim", () => {
  const marker = [].concat(EXTRACTIONS[1].items[0].marker)[0];
  const tampered = read("app.js").replace(marker, "// fileSizeLabel moved.");
  assert.throws(
    () => rebuild({ after: tampered }),
    /marker not found verbatim for fileSizeLabel/,
    "a loosened marker mask could hide an edit, so a changed marker must throw rather than adapt",
  );
});

test("reconstruction REFUSES when an added import line is absent", () => {
  const tampered = read("app.js").replace(EXTRACTIONS[0].importLine, "import { x } from './y.mjs';");
  assert.throws(() => rebuild({ after: tampered }), /import line not found verbatim/);
});

test("reconstruction REFUSES when an extracted function is missing from its module", () => {
  const modules = MODULES();
  modules["util.js"] = modules["util.js"].replace("export function fileSizeLabel", "function fileSizeLabelX");
  assert.throws(() => rebuild({ modules }), /fileSizeLabel not found in util\.js/);
});

test("a PRE-EXISTING export round-trips unchanged", () => {
  // Required before touching mcp/stdio/hermes-managed-host.js, where 11 functions in the first cluster are
  // already `export function`. Their spans are byte-identical with no substitution at all, so the prover
  // must NOT strip a keyword the pristine file contained. Proven both directions on a synthetic pair.
  const pristine = ["const before = 1;", "export function pub(a) {", "  return a;", "}", "const after = 2;", ""].join(LF);
  const host = ["const before = 1;", "// pub moved to ./mod.mjs.", "const after = 2;", ""].join(LF);
  const mod = ["export function pub(a) {", "  return a;", "}", ""].join(LF);

  const kept = reconstruct({
    after: host,
    modules: { "mod.mjs": mod },
    extractions: [{
      module: "mod.mjs",
      items: [{ name: "pub", at: 1, marker: "// pub moved to ./mod.mjs.", pristineExported: true }],
    }],
  });
  assert.equal(kept, pristine, "a pre-existing export must be preserved verbatim");

  // And the default must still STRIP, or every app.js slice would regress.
  const stripped = reconstruct({
    after: host,
    modules: { "mod.mjs": mod },
    extractions: [{
      module: "mod.mjs",
      items: [{ name: "pub", at: 1, marker: "// pub moved to ./mod.mjs." }],
    }],
  });
  assert.match(stripped, /^function pub\(a\) \{$/m, "the default treats `export ` as the added substitution");
  assert.notEqual(stripped, pristine);
});

test("reconstruction REFUSES when pristineExported disagrees with the module", () => {
  // The declaration and the file must not be allowed to drift apart — that is the failure this prover is
  // for. Claiming a pre-existing export for a private function is caught, not silently honoured.
  const mod = ["function priv(a) {", "  return a;", "}", ""].join(LF);
  assert.throws(
    () => reconstruct({
      after: ["// priv moved.", ""].join(LF),
      modules: { "mod.mjs": mod },
      extractions: [{
        module: "mod.mjs",
        items: [{ name: "priv", at: 0, marker: "// priv moved.", pristineExported: true }],
      }],
    }),
    /priv is declared pristineExported but its span in mod\.mjs has no export/,
  );
});

test("functionSpan finds a whole brace-matched body, not the first closing brace", () => {
  const src = "function outer(a) {\n  if (a) {\n    return 1;\n  }\n  return 2;\n}\nfunction after() {}\n";
  const span = functionSpan(src, "outer");
  assert.match(span.text, /return 2;/, "the span must run to the function's own closing brace");
  assert.doesNotMatch(span.text, /function after/);
});
