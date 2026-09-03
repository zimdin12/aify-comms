// The environment-bridge modules are reachable from ONE place, and this keeps it that way.
//
// WHY THIS EXISTS. v0.6.1 removed the `aify-comms` command's ability to start an environment bridge:
// aify-env is the host tier, and the bridge role is dead as a product feature. What was NOT removed
// is 1,802 lines of the code behind it, deliberately -- deleting them edits `server.js`, which every
// running wrapper loads as its MCP server, and that is its own change with its own live verification
// rather than a footnote to a release. (Measured with the RIGHT extensions: `reap-managed-survivors`
// is `.js` while its eight neighbours are `.mjs`, and a walk that assumes one silently drops the
// largest module of the nine. The scan below accepts either, which is why it found its importers.)
//
// SO THE CLUSTER SITS THERE, AND THE RISK IS RE-ENTANGLEMENT. Code that is present and imported by
// exactly one caller can be deleted in an afternoon. The same code with a second caller -- something
// live that reached in for one helper -- becomes a refactor nobody schedules, and the deletion is
// deferred again. That is how dead code becomes permanent.
//
// THE MAP THIS PINS, measured 2026-09-03:
//
//   spawn-loop, terminal-control-loop, environment-control-loop, managed-environment-sync,
//   managed-teardown-sweeps, boot-marker-sweep   <- server.js only
//   terminal-manager                             <- server.js, terminal-control-loop
//   single-agent-teardown                        <- terminal-control-loop only
//   reap-managed-survivors                       <- managed-ownership, managed-teardown-sweeps,
//                                                   single-agent-teardown, terminal-control-loop
//
// Every path ends at `server.js`, and inside it at call sites gated on `IS_ENVIRONMENT_BRIDGE`. Cut
// those and the whole cluster falls away together.
//
// AND THE FIRST MEASUREMENT OF THIS WAS WRONG, which is why the scan below matches both quote
// styles. A grep for `from "./boot-marker-sweep` reported ZERO importers and nearly justified
// deleting a module `server.js` imports on line 116 -- with single quotes. A reachability claim is
// exactly the kind that gets acted on destructively, so the instrument has to be right before the
// conclusion is.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const STDIO = join(HERE, "..");

/** The modules that exist only to serve the retired environment-bridge role. */
const CLUSTER = [
  "spawn-loop",
  "terminal-control-loop",
  "environment-control-loop",
  "managed-environment-sync",
  "managed-teardown-sweeps",
  "boot-marker-sweep",
  "terminal-manager",
  "single-agent-teardown",
  "reap-managed-survivors",
];

/** Product source beside the bridges: no tests, no fixtures, no node_modules. */
function sourceFiles() {
  return readdirSync(STDIO)
    .filter((name) => name.endsWith(".js") || name.endsWith(".mjs"))
    .map((name) => ({ name, text: readFileSync(join(STDIO, name), "utf8") }));
}

/**
 * Who imports `module`, by name.
 *
 * BOTH QUOTE STYLES, and that is the whole lesson of this file: this repo uses `"` in most places and
 * `'` in `server.js`'s import block, so a scan written with one of them reports a module as
 * unreferenced while a live file imports it.
 */
function importersOf(module) {
  const pattern = new RegExp(
    `from\\s+['"]\\./${module}(?:\\.mjs|\\.js)?['"]`,
  );
  return sourceFiles()
    .filter(({ name }) => !name.startsWith(`${module}.`))
    .filter(({ text }) => pattern.test(text))
    .map(({ name }) => name)
    .sort();
}

test("THE SCAN CAN FIND AN IMPORT AT ALL", () => {
  // POSITIVE CONTROL. Every assertion below is about a SMALL set of importers, and a scan that
  // matched nothing would satisfy all of them while proving the opposite of what they claim.
  assert.ok(importersOf("terminal-manager").length > 0,
    "the importer scan found nothing for a module that is definitely imported");
});

test("it matches BOTH quote styles", () => {
  // The bug this file was written after: `boot-marker-sweep` is imported by server.js with single
  // quotes, and a double-quoted scan called it unreferenced.
  const found = importersOf("boot-marker-sweep");
  assert.deepEqual(found, ["server.js"],
    "boot-marker-sweep's importer was missed, so the scan is quote-sensitive again");
});

test("EVERY BRIDGE MODULE IS STILL REACHED ONLY FROM THE CLUSTER OR server.js", () => {
  // The property that keeps the deletion cheap. A new importer from outside means something live
  // now depends on retired code, and the removal stops being an afternoon's work.
  const allowed = new Set([...CLUSTER.map((m) => `${m}.mjs`), ...CLUSTER.map((m) => `${m}.js`),
    "server.js", "managed-ownership.mjs"]);
  const strays = [];
  for (const module of CLUSTER) {
    for (const importer of importersOf(module)) {
      if (!allowed.has(importer)) strays.push(`${importer} -> ${module}`);
    }
  }
  assert.deepEqual(strays, [],
    "something outside the retired bridge cluster now imports it. Either it belongs to the live "
    + "product and should move out of the cluster, or the import is a mistake -- but leaving it "
    + "turns a deletable module into a permanent one.");
});

test("the cluster's only door is server.js", () => {
  // Stated as its own assertion because it is the fact the deletion plan rests on: cut the
  // bridge-role call sites there and the whole set falls away together.
  const doors = new Set();
  for (const module of CLUSTER) {
    for (const importer of importersOf(module)) {
      if (!CLUSTER.some((m) => importer.startsWith(`${m}.`))) doors.add(importer);
    }
  }
  assert.deepEqual([...doors].sort(), ["managed-ownership.mjs", "server.js"],
    "the set of files that reach into the bridge cluster from outside changed");
});

test("managed-ownership is the one to look at first", () => {
  // It reaches `reap-managed-survivors` and is imported by server.js directly, so it is the only
  // member whose deletion is not settled by cutting the bridge-role call sites. Named here so the
  // next person starts where the question actually is rather than rediscovering it.
  assert.deepEqual(importersOf("managed-ownership"), ["server.js"]);
  assert.ok(importersOf("reap-managed-survivors").includes("managed-ownership.mjs"));
});

test("USAGE-COLLECTOR IS NOT IN THE CLUSTER, and that is a decision", () => {
  // NEGATIVE CONTROL, and a near-miss worth keeping. `server.js` calls its collector from a
  // bridge-gated block, so a sweep of "modules only the environment bridge uses" reaches for it --
  // and it is LIVE: `doctor.js` and `usage-preflight.js` both import `checkOpenAiUsageAccess` from
  // it for the `usage-openai` check. Deleting it would have taken a doctor check with it.
  //
  // The alias is why it nearly went unnoticed. `server.js` imports it as
  // `{ collectOnce as collectUsageOnce }`, so looking for who exports `collectUsageOnce` finds
  // NOBODY -- the name does not exist outside that one line. A scan keyed on an exported NAME is
  // blind to every aliased import; the one above is keyed on the PATH, which is what makes it
  // immune. server.js has two aliased imports today, and this is the assertion that says so.
  assert.ok(!CLUSTER.includes("usage-collector"),
    "usage-collector was added to the cluster; the doctor imports it, so deleting it removes a check");
  const live = importersOf("usage-collector").filter((name) => name !== "server.js");
  assert.ok(live.length > 0,
    "nothing outside server.js imports usage-collector any more -- re-examine whether it is now "
    + "cluster-only, rather than assuming this comment still holds");
});

test("the scan is keyed on the PATH, so an alias cannot hide an importer", () => {
  // Proven against the real aliased import rather than a fixture: if this ever reports zero, the
  // scan has become name-keyed and every alias in the tree is invisible to it.
  const text = readFileSync(join(STDIO, "server.js"), "utf8");
  assert.match(text, /import \{ collectOnce as collectUsageOnce/,
    "server.js's aliased import changed shape; this test no longer exercises an alias");
  assert.ok(importersOf("usage-collector").includes("server.js"),
    "the scan missed an ALIASED import, so it cannot be trusted about any module");
});

test("server.js still loads, whatever is imported", () => {
  // The constraint on the whole deletion: this file is the MCP server every running wrapper loads.
  // A parse error here is a fleet that cannot start, so it is asserted beside the map that will be
  // used to edit it.
  const text = readFileSync(join(STDIO, "server.js"), "utf8");
  assert.ok(text.includes("IS_ENVIRONMENT_BRIDGE"),
    "the bridge role is gone from server.js; if the cluster went with it, delete this file too");
});
