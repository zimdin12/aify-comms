#!/usr/bin/env node
// Every skill file is held at its measured size, and the number may only go DOWN.
//
// WHY THIS REPLACES A FLAT CAP. `skill-consistency.test.js` capped four files at round numbers
// (16,000 / 20,000 / 3,500). Two problems, and the operator named both:
//
//   1. **It covered 4 of 17 files.** The other thirteen — ~231 KB, including the whole
//      `aify-comms-debug` reference set and `leading-a-team.md` — could grow unnoticed forever. Two
//      edits grew `leading-a-team.md` by 3 KB in one session and nothing said a word.
//   2. **A round number invites bumping.** An agent that hits "exceeds its 16,000-byte budget" can
//      make the test green by typing 17,000, and that reads like fixing the build. A cap with slack
//      in it is a cap you are allowed to grow into.
//
// A RATCHET fixes both, and this repo already trusts the shape: `no-unwatched-oversized-file.test.js`
// holds install.sh and styles.css at measured values that may only go down, and
// `test_leaves_do_not_import_the_carrier.py` does the same for reconciler imports. The numbers below
// are MEASURED, not rounded up, so growing a file by one byte fails — and the fix is to pay for it
// somewhere, or to change the number deliberately in the same commit and say why.
//
// **Raising a ceiling is a decision, not a repair.** If a skill genuinely needs more room, take it
// from another file, split the file, or write down in the commit what the reader gains for the bytes
// every agent now pays on every turn. What you must not do is nudge the number to make a red test
// green; that is the move this file exists to make visible.
//
// WHY BYTES AT ALL. A skill is not read on demand — the always-loaded ones enter every agent's
// context every session, so a byte there is paid by every agent on every turn rather than once by a
// reader. The references are cheaper (they cost a read when a pointer fires) but not free, and a
// 29 KB reference is roughly 7k tokens for whoever follows the pointer.

import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const SKILLS = path.join(REPO, ".claude", "skills");

// The `.agents/` mirror is byte-identical by `test_skill_mirror_parity.py`, so measuring one side
// measures both. Scanning both would double every number and hide which copy grew.

//: MEASURED 2026-08-19 (re-measured after the debug-reference prune). Not rounded up — see the header. May only go DOWN.
const CEILINGS = {
  "aify-comms/SKILL.md": 15_361,
  "aify-comms/references/building-software.md": 4_488,
  "aify-comms/references/leading-a-team.md": 19_462,
  "aify-comms/references/operations.md": 11_550,
  "aify-comms/references/teamwork.md": 15_981,
  "aify-comms-debug/SKILL.md": 3_117,
  "aify-comms-debug/references/codex.md": 14_114,
  "aify-comms-debug/references/dashboard-console.md": 18_454,
  "aify-comms-debug/references/dispatch-bridges.md": 25917,
  "aify-comms-debug/references/dispatch-delivery.md": 26_577,
  "aify-comms-debug/references/dispatch-launch.md": 14_004,
  "aify-comms-debug/references/hermes-session.md": 27_285,
  "aify-comms-debug/references/hermes-turns.md": 15_996,
  "aify-comms-debug/references/lifecycle.md": 6_704,
  "aify-comms-debug/references/pi.md": 7_316,
  "aify-comms-debug/references/status-model.md": 21_920,
  "aify-comms-debug/references/status-symptoms.md": 19_427,
};

// An ALWAYS-LOADED file enters context whether or not it is needed, so it carries a hard limit on top
// of its ratchet. A reference is reached through a pointer and pays only when that pointer fires.
const ALWAYS_LOADED_LIMIT = 16_000;
const isAlwaysLoaded = (rel) => rel.endsWith("/SKILL.md");

function skillFiles(dir = SKILLS, acc = []) {
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) skillFiles(full, acc);
    else if (entry.endsWith(".md")) acc.push(path.relative(SKILLS, full).replace(/\\/g, "/"));
  }
  return acc;
}

const sizeOf = (rel) => readFileSync(path.join(SKILLS, rel), "utf8").length;

test("the scan reaches the skill tree at all", () => {
  // Without this, a renamed directory turns every assertion below into a vacuous pass.
  const found = skillFiles();
  assert.ok(found.length >= 10, `expected the skill corpus, found ${found.length} file(s)`);
  assert.ok(found.includes("aify-comms/SKILL.md"), "the main skill must be among them");
});

test("every skill file has a ceiling — a new one cannot arrive ungoverned", () => {
  // The hole the flat cap left: thirteen files nobody was watching. A skill added tomorrow inherits
  // the discipline instead of escaping it.
  const unwatched = skillFiles().filter((f) => !(f in CEILINGS)).sort();
  assert.deepEqual(
    unwatched,
    [],
    `skill files with no recorded ceiling: ${unwatched.join(", ")}. Measure them and add them here.`,
  );
});

test("every ceiling names a file that still exists", () => {
  const present = new Set(skillFiles());
  const missing = Object.keys(CEILINGS).filter((f) => !present.has(f)).sort();
  assert.deepEqual(missing, [], `ceilings for files that are gone: ${missing.join(", ")}`);
});

test("no skill file is above its ceiling", () => {
  const over = [];
  for (const [rel, limit] of Object.entries(CEILINGS)) {
    const actual = sizeOf(rel);
    if (actual > limit) over.push(`${rel}: ${actual} > ${limit} (+${actual - limit})`);
  }
  assert.deepEqual(
    over,
    [],
    `skill files grew past their ceiling:\n  ${over.join("\n  ")}\n`
      + "Pay for it elsewhere, split the file, or change the ceiling DELIBERATELY and say why in the "
      + "commit. Raising the number to clear a red test is the move this gate exists to catch.",
  );
});

test("no ceiling is left slack above the file it governs", () => {
  // The half that makes it a ratchet rather than a cap. Shrink a file and the number comes with it,
  // so the recorded size stays the real one and yesterday's headroom cannot be quietly re-spent.
  const slack = [];
  for (const [rel, limit] of Object.entries(CEILINGS)) {
    const actual = sizeOf(rel);
    if (actual < limit) slack.push(`${rel}: ceiling ${limit}, actual ${actual} (lower it by ${limit - actual})`);
  }
  assert.deepEqual(slack, [], `ceilings above their file:\n  ${slack.join("\n  ")}`);
});

test("always-loaded skills stay under the hard limit as well as their ratchet", () => {
  // The ratchet stops growth; this stops a SKILL.md ever being large in the first place, however it
  // got there. Every agent pays these bytes on every turn.
  const over = skillFiles()
    .filter(isAlwaysLoaded)
    .map((rel) => [rel, sizeOf(rel)])
    .filter(([, n]) => n > ALWAYS_LOADED_LIMIT)
    .map(([rel, n]) => `${rel}: ${n} > ${ALWAYS_LOADED_LIMIT}`);
  assert.deepEqual(over, [], `always-loaded skill over the hard limit:\n  ${over.join("\n  ")}`);
});
