#!/usr/bin/env node
// The status vocabulary an agent reasons from is the one this service declares.
//
// TWO SKILLS TABULATE THE AGENT STATUSES, and both are CACHES of `service/contracts/vocabulary.json`
// — the usage skill's operations reference and the debug skill's status model. Both load into every
// agent's context every session, and both are read as the taxonomy: an agent that has never heard of
// `starting` treats a spawn in flight as a dead agent and restarts it, which the contract says in as
// many words kills the boot.
//
// TWO COPIES OF ONE FACT IS THE MECHANISM, and this repo has documented it three separate times for
// its own test counts — twice in CLAUDE.md, once in a paragraph sitting inside the warning about it.
// The answer there was "consider deleting one of them"; here the copies earn their place, because a
// table with an ACTION column is doing something the contract does not. So they stay, and this makes
// them agree.
//
// WHAT IT CHECKS AND WHAT IT CANNOT. It checks the SET of labels, in both directions. It does not
// check that each row's prose still describes the state correctly — that is a reviewer's job, and
// claiming otherwise would be the false green this file exists to prevent. The set is the half that
// can be wrong silently: a status added to the contract and to nothing else leaves every agent on
// the fleet reasoning from a taxonomy that is missing a state.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";

const REPO = path.resolve(new URL("../../..", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const CONTRACT = path.join(REPO, "service", "contracts", "vocabulary.json");

/**
 * The tables, and why each is here.
 *
 * BOTH MIRRORS ARE CHECKED. `.agents/` is a byte-identical copy of `.claude/` gated by
 * `test_skill_mirror_parity.py`, so in principle checking one is enough — but that gate could be the
 * thing that breaks, and a taxonomy that is right for Claude agents and wrong for Codex ones is the
 * shape nobody would look for.
 */
const TABLES = [
  ".claude/skills/aify-comms/references/operations.md",
  ".claude/skills/aify-comms-debug/references/status-model.md",
  ".agents/skills/aify-comms/references/operations.md",
  ".agents/skills/aify-comms-debug/references/status-model.md",
];

/** The statuses this service declares. */
function declared() {
  const contract = JSON.parse(fs.readFileSync(CONTRACT, "utf8"));
  const values = contract?.agentStatuses?.values;
  assert.ok(Array.isArray(values) && values.length > 0, "positive control: the contract declares no statuses");
  return values.map(String);
}

/**
 * The labels a markdown table's FIRST COLUMN names, as backticked codes.
 *
 * SCOPED TO TABLE ROWS, not to the whole document. Both files discuss statuses in prose — "`offline`
 * ≠ `stopped`" is a sentence, not a row — and reading every mention would make this assert that the
 * prose names exactly the eight, which is a different and much more brittle claim.
 */
function tabulated(relative) {
  const full = path.join(REPO, relative);
  assert.ok(fs.existsSync(full), `${relative} is gone; this test now checks nothing`);
  const rows = fs.readFileSync(full, "utf8").split("\n")
    .filter((line) => /^\|\s*`[a-z-]+`\s*\|/.test(line));
  return rows.map((line) => line.match(/^\|\s*`([a-z-]+)`/)[1]);
}

test("POSITIVE CONTROL: every table is found and is not empty", () => {
  // Each assertion below is a set comparison, and an empty set compares equal to an empty set. A
  // renamed heading or a reformatted table would otherwise turn this file green by finding nothing.
  assert.ok(declared().length >= 8, "the contract shrank below the eight states this was written for");
  for (const relative of TABLES) {
    const found = tabulated(relative);
    assert.ok(found.length >= 8, `${relative} tabulates only ${found.length} labels: ${found.join(", ")}`);
  }
});

test("EVERY STATUS THIS SERVICE DECLARES IS IN EVERY SKILL TABLE", () => {
  // The direction that matters most. A status added to the contract and to nothing else leaves every
  // agent reasoning from a taxonomy missing a state — and `starting` is the one that costs something:
  // an agent that has never heard of it reads a spawn in flight as a dead agent and restarts it.
  const ours = declared();
  for (const relative of TABLES) {
    const found = new Set(tabulated(relative));
    const missing = ours.filter((status) => !found.has(status));
    assert.deepEqual(missing, [],
      `${relative} does not tabulate: ${missing.join(", ")}. Every agent loading this skill reasons `
      + "from a taxonomy that is missing a state.");
  }
});

test("NO SKILL TABLE INVENTS A STATUS THIS SERVICE DOES NOT DECLARE", () => {
  // The other direction, which rots more quietly. A label removed from the contract leaves a row
  // describing a state nothing can be in — and an agent will happily match on it and conclude the
  // wrong thing about an agent that is simply in some other state.
  const ours = new Set(declared());
  for (const relative of TABLES) {
    const invented = tabulated(relative).filter((status) => !ours.has(status));
    assert.deepEqual(invented, [],
      `${relative} tabulates statuses this service does not declare: ${invented.join(", ")}`);
  }
});

test("NEGATIVE CONTROL: the comparison can see a difference", () => {
  // Both sweeps pass if the reader returned everything, or if the set arithmetic were inverted.
  const ours = new Set(declared());
  assert.ok(!ours.has("a-status-nobody-declares"));
  const found = new Set(tabulated(TABLES[0]));
  assert.ok(found.has("working"), "the table reader cannot find a label that is certainly there");
  assert.ok(!found.has("a-status-nobody-declares"));
});
