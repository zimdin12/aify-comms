#!/usr/bin/env node
// `IS_ENVIRONMENT_BRIDGE` is gone, and reintroducing it must be a decision rather than an import.
//
// WHY A GATE AT ALL, when the flag is simply deleted. Because the shape that made it dangerous is
// easy to rebuild by accident. Until v0.6.2 twenty-two files consulted a name nobody owned, and by
// the end the three that remained all read it NEGATED — so the flag had no true branch left anywhere
// in the tree, and the only thing it could still do was make the RESIDENT path worse. A stray
// `AIFY_ENVIRONMENT_BRIDGE=1` inherited from anywhere would have made a resident bridge skip its
// auto-registration, skip the harness-death guard that stops it lingering as an orphan, and skip the
// shutdown-when-empty. That is not a dormant flag; it is a live trap on the path every wrapper takes,
// and it fires on the machine of whoever sets that variable for an unrelated reason.
//
// THE ENV VAR IS STILL WRITTEN, DELIBERATELY, and this test says so rather than sweeping it. Three
// places scrub `AIFY_ENVIRONMENT_BRIDGE: "0"` into a child environment. They are not vestigial yet:
// this deletion is INERT until `install.sh` is re-run and every wrapper relaunches, so every bridge
// running on this host executes code that still READS the variable. Removing the scrubs today would
// take the protection away from exactly the processes that still need it.

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

/** Every product source file in the bridge — DERIVED, so a new file is governed on the day it lands. */
function productSources() {
  return readdirSync(STDIO, { withFileTypes: true })
    .filter((e) => e.isFile() && /\.(js|mjs)$/.test(e.name) && !e.name.includes(".test."))
    .map((e) => [e.name, readFileSync(path.join(STDIO, e.name), "utf8")]);
}

/** Source with comments removed, so prose naming the retired flag is not read as a reader of it. */
function codeOnly(src) {
  return src.replace(/^\s*\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");
}

test("no product file READS the retired flag", () => {
  const readers = productSources()
    .filter(([, src]) => /\bIS_ENVIRONMENT_BRIDGE\b/.test(codeOnly(src)))
    .map(([name]) => name);
  assert.deepEqual(
    readers, [],
    `${readers.join(", ")} consults IS_ENVIRONMENT_BRIDGE. There is no environment bridge: the `
    + "command refuses to start one and v0.6.2 deleted the cluster it gated. Every reader that "
    + "remained read it NEGATED, so reintroducing it can only add a branch that fires when a "
    + "resident has inherited the variable by accident — and that branch skips registering the "
    + "agent, guarding against an orphaned bridge, or shutting down when empty.",
  );

  // POSITIVE CONTROL, in the same run: the scan must find a name it should find, or its empty answer
  // above is the walk being broken rather than the flag being gone.
  const seesLive = productSources().some(([, src]) => /\bIS_REMOTE\b/.test(codeOnly(src)));
  assert.ok(seesLive, "the scan cannot see IS_REMOTE, which product files do read — so its zero above is not evidence");

  // NEGATIVE CONTROL: and it must be reading code, not prose. Several files name the retired flag in
  // a comment ON PURPOSE, to record why it went; a scan that fired on those would be unfixable
  // except by deleting the history.
  const inProseOnly = productSources().filter(
    ([, src]) => /\bIS_ENVIRONMENT_BRIDGE\b/.test(src) && !/\bIS_ENVIRONMENT_BRIDGE\b/.test(codeOnly(src)),
  );
  assert.ok(
    inProseOnly.length >= 1,
    "no file mentions the retired flag in prose any more, so the comment-stripping this scan depends "
    + "on is untested — and the reason it was retired has been deleted along with it",
  );
});

test("launch-identity no longer EXPORTS it", () => {
  const src = readFileSync(path.join(STDIO, "launch-identity.mjs"), "utf8");
  assert.ok(
    !/export\s+const\s+IS_ENVIRONMENT_BRIDGE\b/.test(src),
    "launch-identity.mjs exports IS_ENVIRONMENT_BRIDGE again. This module owns the launch identity "
    + "precisely so these names have one home; re-adding it there makes it importable everywhere in "
    + "one line.",
  );
});

test("the env var is still SCRUBBED into children, and that is not an oversight", () => {
  // The one thing this file must not cause: someone reading "the flag is retired" and deleting the
  // scrubs. Every wrapper running right now loads pre-deletion code that READS the variable, because
  // the deletion is inert until install.sh is re-run and each wrapper relaunches.
  const terminalEnv = readFileSync(path.join(STDIO, "terminal-env.js"), "utf8");
  assert.match(
    terminalEnv, /AIFY_ENVIRONMENT_BRIDGE:\s*"0"/,
    "terminal-env.js stopped neutralising AIFY_ENVIRONMENT_BRIDGE for the children it launches. The "
    + "READER is retired in this checkout; the bridges actually running on this host still have it, "
    + "and inheriting a truthy value is how a test process once became the environment bridge and "
    + "reaped seven live gateway hosts.",
  );
  const runtimesProcess = readFileSync(path.join(STDIO, "runtimes-process.js"), "utf8");
  assert.match(
    runtimesProcess, /"AIFY_ENVIRONMENT_BRIDGE"/,
    "runtimes-process.js dropped AIFY_ENVIRONMENT_BRIDGE from the keys it neutralises for a runtime "
    + "child, for the same reason as above",
  );
});
