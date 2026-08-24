// aify-comms' doctor answers questions about aify-comms, and no others.
//
// docs/AIFY_ENV_BOUNDARY.md assigns concerns: aify-wrapper answers "are the launchers installed, are
// they current, do the runtime CLIs exist"; aify-env answers "does node-pty load". Measured against
// that table, four of this doctor's twelve checks were answering other tiers' questions -- and
// `wrappers`, `wrapper-current` and `runtimes` were a SECOND implementation of what
// aify-wrapper-check already does. The second implementation is the one that carried a Windows bug
// (`which` returns an MSYS path native Node cannot open) that aify-wrapper's version never had.
//
// Two implementations of one question do not agree for free; they agree until one is fixed.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const BRIDGE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DOCTOR = readFileSync(path.join(BRIDGE, "doctor.js"), "utf8");

/** Check ids this doctor registers, read from its own `add(...)` calls. */
function registeredCheckIds(source) {
  // `add(` appears bare AND after `return `, because several checks return their verdict directly.
  // Matching only the bare form found 7 of 12 and would have called a still-registered check deleted.
  return [...source.matchAll(/(?:^|[^\w.])add\("([a-z-]+)"/gm)].map((m) => m[1]);
}

// Assigned to another tier by AIFY_ENV_BOUNDARY.md, and already implemented there.
const NOT_OURS = {
  "wrappers": "aify-wrapper — aify-wrapper-check lists installed launchers",
  "wrapper-current": "aify-wrapper — aify-wrapper-check compares them against the registry",
  "runtimes": "aify-wrapper — the boundary table says 'do the runtime CLIs exist'",
  "bridge-terminal": "aify-env — its doctor already reports whether a terminal can be opened",
};

test("the scan finds this doctor's checks at all", () => {
  const ids = registeredCheckIds(DOCTOR);
  assert.ok(ids.length >= 5, `implausibly few checks found: ${ids}`);
  assert.ok(ids.includes("service"), "a known check is missing — the scan is broken");
  assert.equal(ids.includes("not-a-real-check"), false, "the scan must be able to say no");
});

test("it registers no check that belongs to another tier", () => {
  const ids = new Set(registeredCheckIds(DOCTOR));
  const trespassing = Object.keys(NOT_OURS).filter((id) => ids.has(id));
  assert.deepEqual(trespassing, [], (
    "these answer another component's question:\n  "
    + trespassing.map((id) => `${id} -> ${NOT_OURS[id]}`).join("\n  ")
  ));
});

test("the checks that ARE ours are still registered", () => {
  // The deletion must not take the rest with it. These are what the boundary table assigns here.
  const ids = new Set(registeredCheckIds(DOCTOR));
  for (const id of ["service", "bridge-installed", "skills-installed", "env-bridge", "bridge-current"]) {
    assert.ok(ids.has(id), `${id} is aify-comms' own question and must survive`);
  }
});

test("it points at the tool that owns what it stopped answering", () => {
  // A check that disappears with no forwarding address is a worse answer than a duplicated one.
  assert.match(DOCTOR, /aify-wrapper-check/, "it must name where launcher questions are answered now");
  assert.match(DOCTOR, /aify-env doctor/, "and where terminal questions are answered now");
});
