// The `aify-comms` COMMAND is supposed to stop existing. This holds its surface shut while it does.
//
// Phase 8's gate has two clauses (docs/superpowers/plans/2026-08-20-three-repo-separation-roadmap.md):
//
//     aify-comms spawns nothing itself; every spawn goes through aify-env.
//     The `aify-comms` command does not exist.
//
// The FIRST is met -- delegation is on, and `aify-comms doctor`'s `spawn-delegation` reports aify-env
// answering. The SECOND is not: `install.sh` still writes `~/.local/bin/aify-comms`, and on the
// operator's host one has been running since 2026-08-25T04:53 with no owner thinking about it. The
// operator asked what it even is, which is the right question to ask of a component your own
// architecture says was deleted.
//
// WHAT IT STILL HOLDS, measured 2026-08-27 by this file's OWN markers: 17 non-test files, 40
// references. A first pass said 14 and 33 -- it filtered out comment lines and `launch-identity.mjs`,
// where the flag is DEFINED. Both figures were produced minutes apart from the same repo, which is why
// the number quoted here is the one the gate itself computes and not one typed beside it.
// After Phase 8 it stopped doing the WORK and became a relay -- `ensureSpawnLoop` and
// `ensureTerminalControlLoop` forward to aify-env, which does the spawning and owns the PTY. What did
// not move is identity: it registers the environment row, heartbeats it, and parents the managed
// delivery loops. That is why it is both useless and load-bearing, and why deleting it is a piece of
// work rather than an `rm`.
//
// THIS FILE DOES NOT DEMAND THE DELETION. A test that fails until a multi-repo migration lands is red
// for weeks and teaches everyone to skip it -- the failure mode `oversized-allowlist.json` and the
// skill-size ratchet were both built to avoid. It holds the surface at its MEASURED size instead, so
// the count can fall to zero one file at a time and cannot quietly rise. A new `IS_ENVIRONMENT_BRIDGE`
// site is then a decision someone makes on purpose, in a repo where the plan of record is to remove
// the last one.
//
// WHAT THIS GATE CANNOT SEE, stated because the earlier phrasing -- "the surface only shrinks" --
// claimed more than a text search can deliver, and a reviewer said so on 2026-08-27. This is the
// EXPLICIT MARKER surface: the three literals above, nothing else.
//
//   * A module `server.js` imports can be load-bearing for environment-bridge behaviour without
//     spelling any marker. It passes.
//   * TRANSITIVE coupling is invisible: a file reached only through a coupled one is not counted.
//   * Growth is not forbidden, only silenced -- adding a file and listing it here in the same commit
//     passes, on purpose, so the decision is recorded rather than blocked.
//
// A structural census rooted at the environment-bridge boot block would catch the first two. That
// belongs in the deletion plan, where the dependency graph has to be walked anyway; a lexical gate
// bought cheaply today is worth more than a structural one deferred, provided it does not pretend to
// be the structural one.

import test from "node:test";
import assert from "node:assert";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STDIO = path.resolve(HERE, "..");

//: The three ways a file can be coupled to the command: the flag, the env var, and the argv literal.
const MARKERS = [/\bIS_ENVIRONMENT_BRIDGE\b/, /\bAIFY_ENVIRONMENT_BRIDGE\b/, /--environment-bridge/];

//: Non-test bridge files coupled to the environment-bridge command. Measured 2026-08-27.
//: MAY ONLY SHRINK. A name leaving means a responsibility moved to aify-env or to the service, which
//: is the whole plan; a name arriving means the component being retired just grew.
const COUPLED = [
  "auto-registration.mjs",
  "boot-marker-sweep.mjs",
  "bridge-main.mjs",
  "doctor.js",
  "environment-control-loop.mjs",
  "launch-identity.mjs",
  "loop-gate.mjs",
  "managed-environment-sync.mjs",
  "managed-teardown-sweeps.mjs",
  "reap-managed-survivors.js",
  "resident-runtime-lost.mjs",
  "runtimes-process.js",
  "server.js",
  "spawn-loop.mjs",
  "terminal-control-loop.mjs",
  "terminal-env.js",
  "terminal-manager.mjs",
];

function bridgeSources(dir = STDIO, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (["node_modules", "tests", "fixtures", ".git"].includes(entry.name)) continue;
      bridgeSources(path.join(dir, entry.name), out);
      continue;
    }
    if (!/\.(js|mjs)$/.test(entry.name)) continue;
    if (entry.name.includes(".test.")) continue;
    out.push(path.join(dir, entry.name));
  }
  return out;
}

//: Total marker occurrences across those files. Measured 2026-08-27.
//:
//: THE FILE LIST ALONE DID NOT HOLD THIS. Until the ceiling below existed, a 41st marker inside an
//: already-listed file passed the gate while the doc claimed a 17-file / 40-reference surface was
//: held -- a number stated in prose and enforced nowhere, which is the failure this repo keeps
//: rediscovering. Raising it is a decision to write down, not a way to make a red test green.
const REFERENCE_CEILING = 40;

const FILES = bridgeSources();

function markerCount(text) {
  let n = 0;
  for (const m of MARKERS) n += (text.match(new RegExp(m.source, "g")) || []).length;
  return n;
}

const COUNTS = new Map(
  FILES.map((f) => [path.relative(STDIO, f).split(path.sep).join("/"), markerCount(fs.readFileSync(f, "utf8"))]),
);
const COUPLED_NOW = [...COUNTS].filter(([, n]) => n > 0).map(([f]) => f).sort();
const REFERENCES_NOW = [...COUNTS.values()].reduce((a, b) => a + b, 0);

test("the scan can see the bridge at all", () => {
  // POSITIVE CONTROL. A walk that found nothing would make every assertion below pass on empty sets.
  assert.ok(FILES.length > 100, `only ${FILES.length} bridge sources found`);
  assert.ok(
    FILES.some((f) => f.endsWith("server.js")),
    "server.js is not in the walk",
  );
});

test("the marker can say PRESENT and can say ABSENT", () => {
  // A probe that cannot return ABSENT cannot return PRESENT. `server.js` carries the flag;
  // `version.js` is a one-line export that cannot.
  assert.ok(COUPLED_NOW.includes("server.js"), "server.js reads as uncoupled");
  assert.ok(!COUPLED_NOW.includes("version.js"), "version.js reads as coupled; the marker is too loose");
});

test("nothing NEW is coupled to the command being retired", () => {
  const added = COUPLED_NOW.filter((f) => !COUPLED.includes(f));
  assert.deepEqual(
    added,
    [],
    `${JSON.stringify(added)} newly depend on the environment-bridge command. Phase 8's gate is that ` +
      "this command stops existing -- a new coupling site moves it further away. Put the behaviour in " +
      "aify-env (it owns processes and PTYs on this host) or in the service (it owns agent semantics).",
  );
});

test("the reference count does not creep inside the files already listed", () => {
  assert.ok(
    REFERENCES_NOW <= REFERENCE_CEILING,
    `${REFERENCES_NOW} marker references, ceiling ${REFERENCE_CEILING}. The file list cannot see this: ` +
      "adding responsibility to a file that is ALREADY coupled leaves the set unchanged. Lower the " +
      "ceiling as references leave; raising it is a decision, not a repair.",
  );
});

test("the list has not gone stale", () => {
  // It may only shrink, so a name here that is no longer coupled is a decoupling nobody recorded --
  // and a list holding names nobody needs to think about rots into an unchecked one.
  const gone = COUPLED.filter((f) => !COUPLED_NOW.includes(f));
  assert.deepEqual(
    gone,
    [],
    `${JSON.stringify(gone)} are no longer coupled. Remove them from COUPLED: the list may only shrink.`,
  );
});
