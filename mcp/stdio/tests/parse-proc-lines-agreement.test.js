#!/usr/bin/env node
// `parseProcLines` exists twice, byte-identical, and both copies are exported and separately tested.
//
// `proc-probes.js` declares it as part of the shared read-side-of-reaping surface extracted in
// v0.5.4; `reap-managed-claude.js` declares its own copy and does not import that module at all.
// Both are `export`ed, both have their own tests, and the 14 lines are identical character for
// character.
//
// WHAT IT PARSES IS A KILL LIST. Each line is `pid<TAB>ppid<TAB>commandline`, and the reapers decide
// from those rows which process trees to terminate. A change applied to one copy and not the other
// means two reapers disagreeing about what a process IS — and the failure mode of getting that wrong
// is killing a co-located agent's tree, or leaving an orphan holding a session. Neither raises.
//
// HOW IT HID. The fork scan that swept the bridge for duplicated declarations read the top level
// only and could not recognise a `class`; fixing both on 2026-08-16 is what surfaced this. The
// extraction's own note in `proc-probes.js` claimed `reap-managed-claude.js` was a CONSUMER of the
// shared copy — it never was, and that claim is now corrected in place rather than quietly dropped.
//
// AN AGREEMENT TEST, NOT A MERGE. Repointing the reaper at the shared module is the obvious tidy-up
// and is probably right, but this repo's standing rule is that a duplication finding becomes an
// agreement test rather than a forced refactor — the call already made for `createDeferred`, the
// turn-busy reporting family, and `DelegatedManagedController`. Deciding which module OWNS this is a
// reviewer's job; keeping the two from drifting in the meantime is not.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { declarationSpan } from "../../../service/new_dashboard/extraction-proof.mjs";
import { declaringModules } from "./bridge-sources.mjs";
import { parseProcLines as shared } from "../proc-probes.js";
import { parseProcLines as reaper } from "../reap-managed-claude.js";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const NAME = "parseProcLines";
const OWNERS = ["proc-probes.js", "reap-managed-claude.js"];

const spanIn = (relative) => {
  const source = readFileSync(path.join(STDIO, relative), "utf-8").replace(/\r\n/g, "\n");
  const span = declarationSpan(source, NAME);
  assert.ok(span, `${NAME} not found in ${relative}`);
  return span;
};

// ── the sources agree ────────────────────────────────────────────────────────────────────────
{
  const [sharedSpan, reaperSpan] = OWNERS.map(spanIn);
  assert.equal(
    sharedSpan.text,
    reaperSpan.text,
    "the two parseProcLines copies have DIVERGED. Both feed reapers that decide which process trees "
      + "to kill, so a fix in one and not the other means two reapers disagreeing about what a "
      + "process is. Apply it to both, or consolidate and delete this test.",
  );
  assert.equal(sharedSpan.end - sharedSpan.start + 1, 14, "the function changed size — read both");
}

// ── and so do the loaded implementations, on real input ──────────────────────────────────────
{
  // Source identity is not the same claim as behavioural identity: one copy could be shadowed by a
  // re-export, or the modules could differ in something the span does not cover. Run both.
  const samples = [
    "1234\t11\tnode x.js --flag\n5678\t22\tclaude.exe --resume h",
    "",
    "\n\nbad line\n7\t8\tok\n",
    "9\t10\tnode a.js\targ",
  ];
  for (const stdout of samples) {
    assert.deepEqual(
      reaper(stdout),
      shared(stdout),
      `the two implementations disagree on ${JSON.stringify(stdout)}`,
    );
  }
  assert.deepEqual(shared(undefined), [], "and the shared one still tolerates no output at all");
}

// ── exactly two owners, both named ───────────────────────────────────────────────────────────
{
  const declared = declaringModules(NAME).map((d) => d.file).sort();
  assert.deepEqual(
    declared,
    [...OWNERS].sort(),
    "a THIRD copy of parseProcLines appeared, or one moved. Each is another reaper that can drift.",
  );
}

console.log("parse-proc-lines-agreement.test.js: all assertions passed");
