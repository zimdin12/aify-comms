// Source files the two size gates CANNOT SEE, held at a ceiling that may only go down.
//
// THE HOLE THIS CLOSES. `test_no_new_oversized_source_file.py` globs `*.py` under `service/`;
// `no-new-oversized-source-file.test.js` matches `/\.m?js$/`. Between them they cover the twelve files the
// v0.5.4 goal named — and miss `install.sh` at 4,371 lines, which is the LARGEST source file in the repo
// and a first-class product artifact (CLAUDE.md lists it; re-running it is a required release step). A
// file no gate can see can double without any test noticing, and this one nearly did: the whole series ran
// without anybody measuring it.
//
// THIS IS NOT AN EXEMPTION, and the distinction matters. Whether `install.sh` is in scope for the
// 1000-line goal is an operator decision, recorded in docs/OVERSIZED_SCOPE_BLIND_SPOT.md and NOT taken
// here — adding a path to `oversized-allowlist.json` is the reviewer's call, which is the whole point of
// that file. What this does is make the two files VISIBLE and stop them growing while the question is
// open. If the ruling is "in scope", these ceilings become the tracking mechanism; if it is "exempt", they
// become allowlist entries and this file is deleted.
//
// THE PATTERN IS THE REPO'S OWN. `test_leaves_do_not_import_the_carrier.py` holds reconciler borrow-shims
// at a measured ceiling for exactly this reason: "a hard ban here would fail the suite for pre-existing
// debt and teach the next person to weaken the gate instead of paying it". Same shape, same rule about the
// number — it is the MEASURED value, not a comfortable margin above it, so adding a line fails and paying
// one down means lowering this line in the same commit.

import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const LIMIT = 1000;

//: Extensions the two existing size gates do not scan. Kept explicit so adding a new source LANGUAGE to
//: the repo is a deliberate act rather than something this gate silently starts or stops covering.
const UNWATCHED_EXTENSIONS = [".sh", ".css"];

//: MEASURED 2026-08-14, not rounded up. Every entry is pre-existing debt with a pending scope ruling.
const CEILINGS = {
  "install.sh": 4370,
  "service/new_dashboard/styles.css": 1844,
};

const SKIP_DIRS = new Set(["node_modules", ".git", "__pycache__", "dist", "build", ".messages", "data"]);

function sourceFiles() {
  const out = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir)) {
      const full = path.join(dir, entry);
      let stat;
      try {
        stat = statSync(full);
      } catch {
        continue;
      }
      if (stat.isDirectory()) {
        if (!SKIP_DIRS.has(entry)) walk(full);
        continue;
      }
      if (!UNWATCHED_EXTENSIONS.some((ext) => entry.endsWith(ext))) continue;
      if (/\.test\.|(^|[/\\])tests?[/\\]/.test(full)) continue;
      out.push(full);
    }
  };
  walk(REPO);
  return out.sort();
}

const rel = (file) => path.relative(REPO, file).replace(/\\/g, "/");
// `- 1` to match `wc -l` AND both existing size gates, which use exactly this expression. Without it a
// newline-terminated file reads one line longer here than in the gate next door, and two gates reporting
// different numbers for the same file is how a limit stops meaning anything.
const lineCount = (file) => readFileSync(file, "utf-8").split("\n").length - 1;

test("the scan actually reaches these file types — it found install.sh", () => {
  // Anti-vacuity. A walk that skipped the repo root, or an extension list that stopped matching, would make
  // every assertion below pass on an empty set. That is the failure this whole gate exists to prevent, so
  // it must not be the failure the gate itself has.
  const found = sourceFiles().map(rel);
  assert.ok(found.includes("install.sh"), `install.sh must be scanned; found ${found.length} file(s)`);
  assert.ok(found.length >= 2, "at least the two known files must be reachable");
});

test("no UNWATCHED source file exceeds the limit without a recorded ceiling", () => {
  // The real point: a NEW oversized .sh or .css must fail here rather than slip in unseen, which is exactly
  // what happened to install.sh for the whole of v0.5.4.
  const unrecorded = sourceFiles()
    .filter((f) => lineCount(f) > LIMIT)
    .map(rel)
    .filter((r) => !(r in CEILINGS));
  assert.deepEqual(
    unrecorded, [],
    "these are over the 1000-line limit and no gate was watching them:\n  " + unrecorded.join("\n  ")
      + "\nSee docs/OVERSIZED_SCOPE_BLIND_SPOT.md. A ceiling here is a RECORD of pre-existing debt, not "
      + "permission — a genuinely new oversized file should be made smaller instead.",
  );
});

test("each recorded file is at or below its ceiling — the number may only go DOWN", () => {
  for (const [relPath, ceiling] of Object.entries(CEILINGS)) {
    const full = path.join(REPO, relPath);
    assert.ok(existsSync(full), `${relPath} is recorded here but does not exist — remove the entry`);
    const actual = lineCount(full);
    assert.ok(
      actual <= ceiling,
      `${relPath} grew to ${actual}, above its recorded ceiling of ${ceiling}. This file is already over `
        + "the 1000-line limit and outside both size gates; adding to it is not on. If a change legitimately "
        + "needs the lines, say so in the commit and lower another ceiling.",
    );
  }
});

test("a ceiling that has been paid down must be TIGHTENED, not left slack", () => {
  // The other half of a ratchet. A ceiling well above the real count reports success forever — the vacuity
  // failure the reconciler-borrow ceiling names explicitly ("I first wrote 200 against an actual 13").
  for (const [relPath, ceiling] of Object.entries(CEILINGS)) {
    const actual = lineCount(path.join(REPO, relPath));
    assert.ok(
      ceiling - actual <= 20,
      `${relPath} is ${actual} lines against a ceiling of ${ceiling}. Work has been done — lower the `
        + "ceiling to the measured value so the ratchet keeps biting.",
    );
  }
});

test("the pending decision is recorded where someone will find it", () => {
  // These ceilings are a holding position, not an answer. If the packet naming the open question is ever
  // deleted, this gate becomes an unexplained exemption — which is the thing the allowlist's own header
  // warns about.
  const doc = path.join(REPO, "docs", "OVERSIZED_SCOPE_BLIND_SPOT.md");
  assert.ok(existsSync(doc), "the scope ruling packet must exist while these ceilings stand");
  const text = readFileSync(doc, "utf-8");
  for (const relPath of Object.keys(CEILINGS)) {
    assert.ok(text.includes(path.basename(relPath)), `${relPath} must be named in the packet`);
  }
});
