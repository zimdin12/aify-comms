#!/usr/bin/env node
// The WRITE side of reaping is forked, the same way the read side was.
//
// `parse-proc-lines-agreement.test.js` pins `parseProcLines`, declared twice byte-identical, because
// "a reaper that gets identification wrong kills the wrong process tree". That is the READ side.
// This file covers the two helpers that do the killing and the liveness check:
//
//   defaultKillTree   proc-probes.js (export)      vs  hermes-daemon.js (private)   BYTE-IDENTICAL
//   defaultIsPidAlive dead-pty-reporter.js (export)
//   defaultIsAlive    hermes-daemon.js (private)   same logic, different NAME, one delta
//
// The name difference on the second pair is why no scan found it: the fork scans pair declarations
// by name, including after stripping a `Local`/`2` suffix, and `defaultIsAlive` vs
// `defaultIsPidAlive` are simply two different names for one function. It was found by reading.
//
// WHY THIS SUBSYSTEM AND NOT ANOTHER. This project has a standing rule never to blind-kill processes
// by heuristic — Windows reuses pids, and a reaper safety incident is on record. If a guard is added
// to one copy of `defaultKillTree` and not the other (a pid-reuse check, a different signal, a
// dry-run flag), the two subsystems kill differently and nothing fails. The `n <= 0` guard below is
// not cosmetic either: on POSIX `process.kill(0, sig)` signals the caller's whole process GROUP, so
// a zero pid reaching a tree-killer is a self-inflicted outage.
//
// NOTHING HERE KILLS ANYTHING. `defaultKillTree` is called ONLY with pids that its guard rejects
// before any process is touched — that guard is precisely what is being tested. The liveness probe
// is signal-0, which checks existence without signalling.
//
// AN AGREEMENT TEST, NOT A MERGE — the standing answer, already applied to `createDeferred`, the
// turn-busy reporting family, `DelegatedManagedController`, `parseProcLines` and the hermes
// active_list parsers. Which module owns these is a reviewer's call.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { declarationSpan } from "../../../service/new_dashboard/extraction-proof.mjs";
import { defaultKillTree } from "../proc-probes.js";
import { defaultIsPidAlive } from "../dead-pty-reporter.js";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const sourceOf = (rel) => readFileSync(path.join(STDIO, rel), "utf-8").replace(/\r\n/g, "\n");

function bodyOf(rel, name) {
  const src = sourceOf(rel);
  const span = declarationSpan(src, name);
  assert.ok(span, `${name} not found in ${rel} — if it moved or was renamed, repoint this test`);
  return src.split("\n").slice(span.start, span.end + 1).join("\n");
}

const stripExport = (body) => body.replace(/^export\s+/, "");

// ── defaultKillTree: two copies, no delta allowed ────────────────────────────────────────────
{
  const shared = stripExport(bodyOf("proc-probes.js", "defaultKillTree"));
  const daemon = stripExport(bodyOf("hermes-daemon.js", "defaultKillTree"));
  assert.equal(
    daemon, shared,
    "the two tree-killers have drifted. Whatever was added to one — a pid-reuse guard, a different "
      + "signal, a dry-run switch — the other subsystem is still killing the old way, and nothing "
      + "else in the suite would notice.",
  );
}

// ── defaultIsAlive vs defaultIsPidAlive: one KNOWN delta, pinned exactly ─────────────────────
{
  const reporter = stripExport(bodyOf("dead-pty-reporter.js", "defaultIsPidAlive"));
  const daemon = stripExport(bodyOf("hermes-daemon.js", "defaultIsAlive"));

  // Normalise the two names apart, drop comments, and unwrap the ONE difference: the reporter
  // returns Boolean(err && ...), the daemon returns the bare `err && ...`.
  //
  // That delta is not merely inert in practice — it is UNOBSERVABLE. `process.kill` only throws a
  // truthy Error, so the bare form already yields a boolean, and every caller uses the result for
  // truthiness anyway (`if (isAlive(x))`, `!isAlive(x)`). It is normalised away rather than
  // "fixed" because there is nothing to fix and no test could tell the two apart; what this
  // comparison is for is catching the NEXT difference, which may not be harmless.
  const normalise = (body, name) =>
    body
      .replace(new RegExp(`\\b${name}\\b`), "NAME")
      .replace(/^\s*\/\/.*$/gm, "")
      .replace(/Boolean\((err && err\.code === "EPERM")\)/, "$1")
      .replace(/\s+/g, " ")
      .trim();

  assert.equal(
    normalise(daemon, "defaultIsAlive"), normalise(reporter, "defaultIsPidAlive"),
    "the liveness probes have drifted beyond the known Boolean() wrapper. Two answers to 'is this "
      + "pid alive' means one reaper spares a process the other reaps.",
  );
}

// ── the guard that stops a tree-killer touching anything ─────────────────────────────────────
{
  // Every input here is REJECTED BY THE GUARD, so no process is signalled. That is the whole point:
  // pid 0 on POSIX means "my entire process group".
  for (const bad of [0, -1, -99, 1.5, NaN, Infinity, null, undefined, "", "abc", {}, []]) {
    assert.equal(
      defaultKillTree(bad), false,
      `defaultKillTree(${JSON.stringify(bad)}) must refuse before touching a process`,
    );
  }
}

// ── the liveness probe, on pids it is safe to ask about ──────────────────────────────────────
{
  // Only the dead-pty copy is exported; hermes-daemon's is private, which is why its half of this
  // file is a source comparison rather than a call. Testing through the public surface instead of
  // exporting a function so a test can reach it.
  assert.equal(defaultIsPidAlive(process.pid), true, "this very process is alive");
  for (const bad of [0, -1, 1.5, NaN, null, undefined, "", "abc"]) {
    assert.equal(defaultIsPidAlive(bad), false, `rejected pid: ${JSON.stringify(bad)}`);
  }
  // Returns a real boolean for every reachable input.
  //
  // BUT NOT BECAUSE OF THE Boolean() WRAPPER, and this file will not pretend otherwise. Removing
  // that wrapper changes nothing observable: `process.kill` only ever throws a truthy Error, so
  // `err && err.code === "EPERM"` already evaluates to a boolean. Confirmed by mutation — deleting
  // `Boolean(...)` leaves every assertion here green, because no input the runtime can produce
  // reaches the falsy-`err` case. The two spellings are indistinguishable by test; the source pin
  // above is the only thing that would catch a NEW divergence between them.
  for (const value of [process.pid, 0, "abc"]) {
    assert.equal(typeof defaultIsPidAlive(value), "boolean", `typeof for ${JSON.stringify(value)}`);
  }
}

// ── anti-vacuity ─────────────────────────────────────────────────────────────────────────────
{
  // The guard assertions above would all pass against a `defaultKillTree` that returned false for
  // everything, and the liveness ones against a probe that returned false for everything. The
  // positive case is what stops that.
  assert.equal(defaultIsPidAlive(process.pid), true);
  assert.notEqual(defaultIsPidAlive(process.pid), defaultIsPidAlive(0));
}

console.log("process-kill-helpers-agree.test.js: all assertions passed");
