// `bridgeSources()` decides what "the bridge" is for every gate built on it. Nothing checked it.
//
// It is the population for the dead-import sweep, the one-owner check, the used-anywhere probe and
// the missing-sibling-import gate. A file outside it is not merely unchecked — it reports GREEN,
// which is indistinguishable from checked-and-clean. That is the failure CLAUDE.md records on the
// Python side, where the size gate read `service/**` only and left fifteen files ungoverned,
// `mcp/sse_server.py` among them.
//
// THE HOLE THIS CLOSES. `SKIP_DIRS` excluded `scripts/` with no reason written down, while the two
// exclusions beside it earn their place in the file: `tests/` is a different subject, and
// `fixtures/` holds pre-extraction copies whose presence would let an absence assertion pass off the
// OLD file. `install.sh` copies `mcp/stdio` wholesale into `~/.aify-comms`, so
// `scripts/dump-capabilities.mjs` ships to every host exactly like the rest of the bridge.
//
// The missing-sibling-import gate had the same shape one level up: it called `bridgeSources()` and
// then added `adapters/` and `controllers/` from a HAND-WRITTEN list, which by construction could
// not know about a third directory. Both are now one walk.
//
// So this test compares the population against the filesystem rather than against a list. A new
// subdirectory is then a red test that names it, instead of a quiet exemption.

import assert from "node:assert/strict";
import test from "node:test";
import { readdirSync } from "node:fs";
import path from "node:path";

import { bridgeSources, STDIO_DIR } from "./bridge-sources.mjs";

//: Directories deliberately outside the bridge population, each with the reason it is out. The
//: point is that a reason EXISTS and is checkable — `scripts/` was excluded with none, and that is
//: what let a shipped directory sit outside every gate.
const EXCLUDED_DIRS = {
  "node_modules": "third-party code the bridge depends on, not code the bridge is",
  tests: "a different subject: the gates, not the thing gated",
  fixtures: "pre-extraction snapshots — including them would let an absence assertion pass off the OLD file",
  __pycache__: "not JavaScript",
};

//: RUNTIME ARTEFACTS, not source: `.messages/` is the bridge's on-disk inbox and `.pytest_cache/`
//: is a tool's scratch. Both are gitignored and appear only on a machine that has RUN the bridge, so
//: naming them individually would make this gate pass on a clean checkout and fail on a working one.
//: A dot-prefixed directory is the repo's convention for "not source" — `.claude/`, `.agents/`,
//: `.git/` — so the rule keys on that rather than on a list nobody can complete.
const isRuntimeArtefact = (name) => name.startsWith(".");

/** Every non-test JS/MJS file under `mcp/stdio`, from the filesystem, ignoring the gate's own view. */
function walkFilesystem(dir = STDIO_DIR, prefix = "") {
  const found = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) {
      if (entry.name in EXCLUDED_DIRS || isRuntimeArtefact(entry.name)) continue;
      found.push(...walkFilesystem(path.join(dir, entry.name), relative));
      continue;
    }
    if (!/\.(mjs|js)$/.test(entry.name) || entry.name.includes(".test.")) continue;
    found.push(relative);
  }
  return found;
}

test("the bridge population IS the filesystem, not a hand-maintained list", () => {
  const declared = bridgeSources().map(([file]) => file).sort();
  const actual = walkFilesystem().sort();
  assert.deepEqual(
    declared, actual,
    "bridgeSources() and the filesystem disagree. A file it does not yield is not unchecked — it "
    + "reports GREEN through every gate built on this. If a directory genuinely belongs outside, add "
    + "it to EXCLUDED_DIRS here WITH the reason, the way tests/ and fixtures/ carry theirs.",
  );
});

test("every subdirectory that exists is either scanned or excluded WITH a reason", () => {
  const subdirectories = readdirSync(STDIO_DIR, { withFileTypes: true })
    .filter((e) => e.isDirectory() && !isRuntimeArtefact(e.name))
    .map((e) => e.name);
  assert.ok(subdirectories.length >= 3, `only ${subdirectories.length} subdirectories found`);

  const scanned = new Set(
    bridgeSources()
      .map(([file]) => (file.includes("/") ? file.split("/")[0] : null))
      .filter(Boolean),
  );
  for (const name of subdirectories) {
    const excluded = name in EXCLUDED_DIRS;
    assert.ok(
      scanned.has(name) || excluded,
      `mcp/stdio/${name}/ is neither scanned nor excluded — it ships with the bridge and every gate `
      + "built on bridgeSources() currently reports it green without reading it",
    );
    if (excluded) {
      assert.ok(EXCLUDED_DIRS[name].length > 20, `${name} is excluded with no real reason given`);
    }
  }
});

test("the directory that was silently outside is now inside", () => {
  // Named, because a general property is easy to satisfy vacuously and this is the specific file
  // that shipped to every host through `install.sh` while no gate could see it.
  const files = bridgeSources().map(([file]) => file);
  assert.ok(
    files.includes("scripts/dump-capabilities.mjs"),
    "scripts/ is back outside the population — install.sh copies mcp/stdio wholesale, so it ships",
  );
  assert.ok(files.some((f) => f.startsWith("adapters/")), "adapters/ must stay in");
  assert.ok(files.some((f) => f.startsWith("controllers/")), "controllers/ must stay in");
});

test("the excluded directories really are excluded, or the reasons are decoration", () => {
  // Anti-vacuity from the other side: if `bridgeSources()` yielded everything, the first test would
  // pass and the exclusions would mean nothing.
  const files = bridgeSources().map(([file]) => file);
  for (const name of Object.keys(EXCLUDED_DIRS)) {
    assert.ok(
      !files.some((f) => f.startsWith(`${name}/`)),
      `${name}/ is excluded in the ledger but present in the population`,
    );
  }
  // `fixtures/` is the one whose exclusion is load-bearing: it holds copies of code a slice removed.
  assert.ok(
    !files.some((f) => f.includes("fixtures")),
    "a fixture in the population would let an absence assertion pass off a pre-extraction copy",
  );
});
