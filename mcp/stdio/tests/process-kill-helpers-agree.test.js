#!/usr/bin/env node
// The WRITE side of reaping is forked, the same way the read side was.
//
// `parse-proc-lines-agreement.test.js` pins `parseProcLines`, declared twice byte-identical, because
// "a reaper that gets identification wrong kills the wrong process tree". That is the READ side.
// This file covers the two helpers that do the killing and the liveness check:
//
//   defaultKillTree   proc-probes.js (export)      vs  hermes-daemon.js (private)   BYTE-IDENTICAL
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

// The liveness-probe half of this file went with dead-pty-reporter.js on 2026-09-17: nothing imported
// that module after v0.6.2, so its copy of the probe had no caller to drift from.

console.log("process-kill-helpers-agree.test.js: all assertions passed");
