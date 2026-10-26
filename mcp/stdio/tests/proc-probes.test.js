#!/usr/bin/env node
// Tests that CALL `proc-probes.js`: `parseProcLines`, which turns a tab-separated process listing into rows and is
// the input every match `reap-managed-claude.js` makes runs against. (The hermes probes and the tree kill left with
// the gateway start in 0.9.10, and their tests with them.)

import assert from "node:assert/strict";

import { parseProcLines } from "../proc-probes.js";

// ── parseProcLines: PID, PPID, CMDLINE ───────────────────────────────────────────────────────
{
  const rows = parseProcLines("1234\t11\tnode x.js --flag\n5678\t22\tclaude.exe --resume h");
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0], { pid: 1234, ppid: 11, commandLine: "node x.js --flag" });
  assert.equal(rows[1].pid, 5678);
  assert.equal(rows[1].commandLine, "claude.exe --resume h");

  assert.deepEqual(parseProcLines(""), [], "no output is no rows, not a throw");
  assert.deepEqual(parseProcLines(undefined), [], "and neither is undefined");

  const messy = parseProcLines("\n\nbad line\n7\t8\tok\n");
  assert.deepEqual(messy, [{ pid: 7, ppid: 8, commandLine: "ok" }],
    "blank and malformed lines are dropped rather than becoming rows with NaN pids — a NaN pid is a "
    + "kill target that matches nothing, or worse, something");

  const tabsInCmdline = parseProcLines("9\t10\tnode a.js\targ");
  assert.equal(tabsInCmdline[0].commandLine, "node a.js\targ",
    "a tab INSIDE the command line survives — only the first two are field separators");
}

console.log("proc-probes.test.js: all assertions passed");
