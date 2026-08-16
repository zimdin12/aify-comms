#!/usr/bin/env node
// The environment-status vocabulary is written in two languages and must mean the same thing.
//
// `service/env_status.py` owns it: `ENVIRONMENT_STATUSES` is every status an environment row can
// hold, and `ENVIRONMENT_REGISTRABLE_STATUSES` is the subset a bridge may ask for. The doctor's
// `ENV_KNOWN_STATES` is a hand-typed copy of the first, and its `envStateIsUnknown` decides whether
// the operator is told "this environment is in a state I do not recognise".
//
// NOTHING TIED THE TWO. Add a status on the Python side and the doctor calls it unknown — a check
// whose whole job is to fail loudly failing about the wrong thing, on every environment, until
// someone notices the second list. Both constants were also named by no test, which is what
// `every-export-is-named-by-a-test.test.js` surfaced.
//
// THE ASSERTIONS ARE BEHAVIOURAL, not a literal-vs-literal comparison. The Python file supplies the
// vocabulary and each status is then run through the doctor's own predicate — so this fails if the
// copy drifts OR if the predicate stops using it, which a source-to-source diff could not see.
//
// ENV_CONNECTED_STATES IS DELIBERATELY NARROWER and that is not drift. "Known" is every state a row
// can be in; "connected" is the one state that can host a managed spawn, which is what the
// env-bridge check claims to prove. Counting `degraded` as connected once let doctor read green
// while no spawn could run. Asserted as a strict subset, with the reason, so the difference cannot
// be "tidied up".

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  ENV_CONNECTED_STATES,
  ENV_KNOWN_STATES,
  envIsOnlineAt,
  envStateIsUnknown,
} from "../doctor-predicates.js";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const ENV_STATUS_PY = path.join(REPO, "service", "env_status.py");

/** The members of a `NAME = frozenset({...})` line, read from the module that owns them. */
function pythonFrozenset(name) {
  const source = readFileSync(ENV_STATUS_PY, "utf-8");
  const line = source.split("\n").find((l) => l.startsWith(`${name} = frozenset(`));
  assert.ok(line, `${name} not found in service/env_status.py — has it moved or been renamed?`);
  const inner = line.slice(line.indexOf("{") + 1, line.lastIndexOf("}"));
  const members = [...inner.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  assert.ok(members.length > 0, `${name} parsed as empty`);
  return members;
}

test("the parse finds a real vocabulary, not an empty one", () => {
  // Anti-vacuity: every assertion below iterates this list, so an empty parse would pass them all.
  const all = pythonFrozenset("ENVIRONMENT_STATUSES");
  const registrable = pythonFrozenset("ENVIRONMENT_REGISTRABLE_STATUSES");
  assert.ok(all.length >= 5, `parsed only ${all.length} statuses`);
  assert.ok(registrable.length >= 3, `parsed only ${registrable.length} registrable statuses`);
  assert.ok(all.includes("online") && all.includes("forgotten"));
});

test("every status the service can store is one the doctor recognises", () => {
  for (const status of pythonFrozenset("ENVIRONMENT_STATUSES")) {
    assert.equal(
      envStateIsUnknown({ id: "e", status }), false,
      `the service can write status "${status}" and the doctor reports it as unrecognised — it `
        + "would say so about every environment in that state",
    );
  }
});

test("the doctor knows nothing the service cannot store", () => {
  // The other direction: a stale name left in the JS copy reads as recognised forever, so an
  // environment in a status that no longer exists would never be flagged.
  const owned = new Set(pythonFrozenset("ENVIRONMENT_STATUSES"));
  const extra = [...ENV_KNOWN_STATES].filter((s) => !owned.has(s));
  assert.deepEqual(extra, [], "doctor knows statuses service/env_status.py does not define");
});

test("a status neither side defines is reported as unknown", () => {
  for (const status of ["sleeping", "stale", "", "ONLINE-ish", "unknown"]) {
    assert.equal(
      envStateIsUnknown({ id: "e", status }), true,
      `"${status}" was treated as a recognised state`,
    );
  }
});

test("recognition folds case and whitespace, like every other reader of this column", () => {
  for (const status of ["ONLINE", " Online ", "OFFLINE", "\tdegraded"]) {
    assert.equal(envStateIsUnknown({ id: "e", status }), false, `"${status}" was not recognised`);
  }
});

test("CONNECTED is a strict subset of KNOWN, and only online can host a spawn", () => {
  const known = new Set(pythonFrozenset("ENVIRONMENT_STATUSES"));
  for (const status of ENV_CONNECTED_STATES) {
    assert.ok(known.has(status), `"${status}" is connected but not a status the service can store`);
  }
  assert.ok(ENV_CONNECTED_STATES.size < ENV_KNOWN_STATES.size, "connected must be the narrower set");
  assert.deepEqual(
    [...ENV_CONNECTED_STATES], ["online"],
    "the spawn picker skips anything that is not exactly online, so widening this reports green "
      + "while no managed spawn can actually run",
  );
});

test("a registrable status a bridge may claim is always a recognised one", () => {
  // The bridge can only ASK for this subset, so any member being unrecognised would mean doctor
  // flagging a state the bridge just legitimately requested.
  for (const status of pythonFrozenset("ENVIRONMENT_REGISTRABLE_STATUSES")) {
    assert.equal(envStateIsUnknown({ id: "e", status }), false, `registrable "${status}" is unknown`);
    assert.ok(ENV_KNOWN_STATES.has(status));
  }
});

test("degraded is KNOWN but not CONNECTED — the distinction, driven end to end", () => {
  const now = Date.UTC(2026, 7, 16, 12, 0, 0);
  const fresh = new Date(now - 5_000).toISOString();
  assert.equal(envStateIsUnknown({ id: "e", status: "degraded" }), false);
  assert.equal(
    envIsOnlineAt({ id: "e", status: "degraded", lastSeen: fresh }, now), false,
    "a degraded environment cannot host a managed spawn, however recently it beat",
  );
  assert.equal(envIsOnlineAt({ id: "e", status: "online", lastSeen: fresh }, now), true);
});
