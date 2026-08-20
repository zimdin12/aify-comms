// Every environment variable a launcher EXPORTS has a reader in this repo.
//
// The templates live in the aify-wrapper package now, pinned to a sha. One source of truth for the
// launcher text is the point -- and it also means a rename over there arrives as a version bump nobody
// reads line by line. An exported name nothing reads is config that silently stopped arriving: the
// reader falls back to its default, the agent starts, and nothing is red. Same one-end-asserted shape
// as the endpoint-reader drift and the two installers baking different meanings into one marker.
//
// READERS ARE NOT ALL IN THE BRIDGE, and a first version of this test asserted they were. It reported
// three defects that were not defects: AIFY_EXPLICIT_SESSION_HANDLE is read in mcp/stdio/adapters/,
// which the scan did not descend into; AIFY_HERMES_GATEWAY_TOKEN is reached through the
// `*_TOKEN_ENV` indirection, which no `process.env.NAME` pattern can see; and AIFY_HERMES_PLUGIN is
// read by the hermes plugin, which is Python. So this looks for the NAME anywhere a reader could live,
// which is weaker per-name and the strongest claim the evidence supports.
//
// Direction matters. EXPORT -> READER only. The reverse is not a defect: ~85 AIFY_* names are read
// that no launcher sets, because the environment bridge supplies them when it spawns a managed worker
// or they are operator tuning knobs with defaults.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BRIDGE = path.resolve(HERE, "..");
const REPO = path.resolve(BRIDGE, "..", "..");
const TEMPLATES = path.join(BRIDGE, "node_modules", "aify-wrapper", "wrappers");

// `export NAME=` with any leading whitespace. Only EXPORTED names cross into the runtime's
// environment; a plain `NAME=` is a shell local the launcher uses to build its own command line, and
// no reader could see it however hard it looked.
const EXPORTED = /^[ \t]*export[ \t]+(AIFY_[A-Z0-9_]+)=/gm;

// Where a reader can live. install.sh and the templates are excluded deliberately: they WRITE these
// names, so counting them would make every assertion below pass by finding the producer.
const READER_ROOTS = ["mcp/stdio", "integrations", "service"];
const READER_EXT = [".js", ".mjs", ".py"];
const SKIP_DIRS = new Set(["node_modules", "tests", "fixtures", "__pycache__", ".git", "new_dashboard"]);

export function exportedNames(dir = TEMPLATES) {
  const found = new Set();
  for (const entry of fs.readdirSync(dir)) {
    if (!entry.endsWith(".sh.in")) continue;
    const text = fs.readFileSync(path.join(dir, entry), "utf8");
    for (const match of text.matchAll(EXPORTED)) found.add(match[1]);
  }
  return found;
}

function readerText() {
  let all = "";
  const walk = (dir) => {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      if (entry.isDirectory()) {
        if (!SKIP_DIRS.has(entry.name)) walk(path.join(dir, entry.name));
      } else if (READER_EXT.some((e) => entry.name.endsWith(e))) {
        all += fs.readFileSync(path.join(dir, entry.name), "utf8");
      }
    }
  };
  for (const root of READER_ROOTS) walk(path.join(REPO, root));
  return all;
}

test("both scans find what is known to be there, or every result below is vacuous", () => {
  if (!fs.existsSync(TEMPLATES)) {
    // A checkout that has not run `npm install` cannot answer this, and must not report a pass.
    assert.fail("aify-wrapper package not installed — run 'npm install' in mcp/stdio");
  }
  const exports = exportedNames();
  assert.ok(exports.size >= 4, `implausibly few exported names: ${[...exports]}`);
  assert.ok(exports.has("AIFY_SERVER_URL"), "the endpoint export is missing from the scan");
  assert.equal(exports.has("AIFY_NOT_A_REAL_NAME"), false, "the export scan must be able to say no");

  const text = readerText();
  assert.ok(text.length > 100_000, `implausibly little reader source scanned: ${text.length} bytes`);
  assert.ok(text.includes("AIFY_SERVER_URL"), "a known reader is missing from the scan");
  assert.equal(text.includes("AIFY_NOT_A_REAL_NAME"), false, "the reader scan must be able to say no");
});

test("every name a launcher exports has a reader", () => {
  const text = readerText();
  const orphans = [...exportedNames()].filter((name) => !text.includes(name)).sort();
  assert.deepEqual(orphans, [], (
    `these are exported into the runtime's environment and read by nothing: ${orphans}. `
    + "Either a reader stopped reading one, or the template renamed it — both look identical from "
    + "outside, because the reader falls back to its default and the agent starts."
  ));
});

test("the orphan check can actually find one, against input it is given", () => {
  // Watched to fail rather than assumed. A synthetic template dir, so nothing shared is mutated --
  // rendering happens from the real one while suites run, and swapping a file under it is the race
  // this project keeps almost causing.
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-exports-"));
  fs.writeFileSync(path.join(dir, "probe-aify.sh.in"), [
    "#!/bin/bash",
    "export AIFY_READ_BY_SOMETHING=1",
    "  export AIFY_DEFINITELY_NOBODY_READS_THIS=1",
    "AIFY_A_SHELL_LOCAL=1",
  ].join(String.fromCharCode(10)));

  const names = exportedNames(dir);
  assert.ok(names.has("AIFY_READ_BY_SOMETHING"), "an unindented export must be found");
  assert.ok(names.has("AIFY_DEFINITELY_NOBODY_READS_THIS"), "an indented export must be found too");
  assert.equal(names.has("AIFY_A_SHELL_LOCAL"), false,
    "a plain assignment never reaches the runtime's environment, so it is not this test's business");

  const pretendReaders = "const x = process.env.AIFY_READ_BY_SOMETHING;";
  const orphans = [...names].filter((n) => !pretendReaders.includes(n)).sort();
  assert.deepEqual(orphans, ["AIFY_DEFINITELY_NOBODY_READS_THIS"],
    "the orphan path must report exactly the name with no reader");
});
