// The bridge's per-agent state, and the invariant that keeps its three Maps consistent.
//
// `REMOTE_AGENT_STATE`, `ACTIVE_RUNS` and `CONSECUTIVE_FAILURES` are reset together and forgotten
// together. Applied to a subset, either operation leaves the bridge believing in a run whose agent it has
// forgotten, or backing off for an agent it no longer serves. Until v0.5.4 all three lived in
// `server.js`, the bin entry point, which nothing imports — so the invariant had no test at all, and a
// Map added to one reset and not the other was silent.

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  ACTIVE_RUNS,
  CONSECUTIVE_FAILURES,
  REMOTE_AGENT_STATE,
  forgetRemoteAgent,
  interruptActiveRuns,
} from "../bridge-agent-state.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const OWNED = [
  ["REMOTE_AGENT_STATE", REMOTE_AGENT_STATE],
  ["ACTIVE_RUNS", ACTIVE_RUNS],
  ["CONSECUTIVE_FAILURES", CONSECUTIVE_FAILURES],
];

// TWO agents in every Map, always. A clear or a delete tested against a single entry — or an empty Map —
// passes without distinguishing "removed the right one" from "removed everything" or "did nothing".
function seedTwo() {
  for (const [, map] of OWNED) map.clear();
  for (const id of ["agent-a", "agent-b"]) {
    REMOTE_AGENT_STATE.set(id, { info: { role: "coder" } });
    ACTIVE_RUNS.set(id, { runId: `run-${id}`, runtime: "codex", controller: {} });
    CONSECUTIVE_FAILURES.set(id, 3);
  }
  for (const [name, map] of OWNED) assert.equal(map.size, 2, `${name} must be seeded before the assertion`);
}

test("all three are Maps, and importing the module constructs them empty", () => {
  for (const [name, map] of OWNED) {
    assert.ok(map instanceof Map, `${name} must be a Map`);
  }
  assert.equal(new Set(OWNED.map(([, m]) => m)).size, 3, "three distinct Maps, not one aliased three ways");
});

test("forgetRemoteAgent removes that agent from all three, and leaves the other alone", () => {
  seedTwo();
  forgetRemoteAgent("agent-a");
  for (const [name, map] of OWNED) {
    assert.equal(map.has("agent-a"), false, `${name} must forget agent-a`);
    assert.equal(map.has("agent-b"), true, `${name} must NOT have forgotten agent-b`);
  }
});

test("forgetRemoteAgent on an unknown agent is a no-op, not a reset", () => {
  // The failure this catches: a "forget" implemented as a clear would pass every assertion above while
  // destroying the whole fleet's state on a stray call.
  seedTwo();
  forgetRemoteAgent("never-registered");
  for (const [name, map] of OWNED) assert.equal(map.size, 2, `${name} must be untouched`);
});

test("the reason string is optional and does not change what is deleted", () => {
  seedTwo();
  forgetRemoteAgent("agent-a", "superseded by a newer bridge");
  for (const [name, map] of OWNED) {
    assert.equal(map.has("agent-a"), false, `${name} must forget agent-a with a reason given too`);
    assert.equal(map.has("agent-b"), true, `${name} must still hold agent-b`);
  }
});

test("THE RESET SET IS EXACTLY THESE THREE — a fourth coupled Map must fail this", () => {
  // The invariant nothing could see before. `comms_clear`'s all-agents branch clears three Maps by name.
  // If a fourth per-agent Map is later added to that branch, or one of these is dropped from it, this
  // assertion is what notices. It is deliberately keyed on the OWNER module's contents rather than a
  // hardcoded list, so the two cannot drift apart.
  const src = readFileSync(path.join(STDIO, "bridge-agent-state.mjs"), "utf-8");
  const exported = [...src.matchAll(/^export const (\w+) = new Map\(\);$/gm)].map((m) => m[1]);
  assert.deepEqual(
    exported.sort(), ["ACTIVE_RUNS", "CONSECUTIVE_FAILURES", "REMOTE_AGENT_STATE"],
    "the owner module's Map set changed — the reset branch in comms_clear must be checked against it",
  );

  // And every owned Map must actually be deleted from by forgetRemoteAgent. A Map added to this module
  // but not to the forget operation is the per-agent half of the same drift.
  const forget = src.slice(src.indexOf("export function forgetRemoteAgent"));
  for (const name of exported) {
    assert.match(forget, new RegExp(`${name}\\.delete\\(agentId\\)`), `forgetRemoteAgent must delete from ${name}`);
  }

  // The all-agents reset must clear exactly the owned set, no more and no less.
  //
  // Scanned across the bridge rather than in server.js. I wrote this against server.js and it went red
  // one commit later when comms_clear moved to `lifecycle-tools.mjs` — the SIXTH time in this lane that
  // an assertion of mine measured where code LIVES instead of what it does. The reset can live anywhere;
  // what must hold is that whoever performs it covers every owned Map.
  const cleared = readdirSync(STDIO)
    .filter((name) => /\.(js|mjs)$/.test(name))
    .flatMap((name) => [
      ...readFileSync(path.join(STDIO, name), "utf-8").matchAll(/^\s+(\w+)\.clear\(\);$/gm),
    ].map((m) => m[1]));
  const ownedCleared = cleared.filter((n) => exported.includes(n));
  assert.deepEqual(
    [...new Set(ownedCleared)].sort(), exported,
    "comms_clear's all-agents reset must clear every owned Map — a subset leaves inconsistent state",
  );
});

test("LOCAL_RUNTIME_STATE stayed in server.js, and is not part of this invariant", () => {
  // It is also a per-agent Map declared two lines from these, and it deliberately did not come along: it
  // is local-mode auto-start state, has one writer, and is in NEITHER reset. Type is not subject.
  //
  // Asserted as DECLARATION and USE, not as the bare word — the owner module's header explains at length
  // why this Map did not come along, and a word-absence check fails on that explanation. Fifth time in
  // this lane that a negative proof punished the documentation of the invariant it protects; the rule is
  // now unconditional: never assert a name is absent, assert the thing it would DO is absent.
  const owner = readFileSync(path.join(STDIO, "bridge-agent-state.mjs"), "utf-8");
  assert.doesNotMatch(owner, /^(?:export\s+)?const LOCAL_RUNTIME_STATE\b/m, "this owner must not declare it");
  assert.doesNotMatch(owner, /LOCAL_RUNTIME_STATE\.(get|set|delete|clear|has)\s*\(/, "…nor touch it");
  const server = readFileSync(path.join(STDIO, "server.js"), "utf-8");
  assert.match(server, /^const LOCAL_RUNTIME_STATE = new Map\(\);$/m, "it must still be declared in server.js");
});

test("server.js declares none of the four names — exactly one owner", () => {
  // A leftover declaration would SHADOW the import and keep working, until the bridge held two Maps under
  // one name and half its readers used each. That is silent, not a crash.
  const src = readFileSync(path.join(STDIO, "server.js"), "utf-8");
  for (const name of ["REMOTE_AGENT_STATE", "ACTIVE_RUNS", "CONSECUTIVE_FAILURES"]) {
    assert.doesNotMatch(src, new RegExp(`^(?:const|let|var)\\s+${name}\\b`, "m"), `${name} must be imported`);
    assert.match(src, new RegExp(`(?<![\\w.])${name}(?![\\w])`), `server.js is still expected to USE ${name}`);
  }
  assert.doesNotMatch(src, /^(?:export\s+)?function\s+forgetRemoteAgent\b/m, "forgetRemoteAgent must be imported");
  assert.match(src, /(?<![\w.])forgetRemoteAgent\(/, "server.js is still expected to CALL it");
});

test("the owner is a state owner, not a service layer", () => {
  // Around thirty functions read these Maps and every one stayed where it was. Pulling them in would
  // recreate the monolith at a new address, which is the failure mode this whole lane exists to avoid.
  const src = readFileSync(path.join(STDIO, "bridge-agent-state.mjs"), "utf-8");
  assert.ok(!/^import\s/m.test(src), "three Maps and a delete need no dependencies");
  assert.equal(
    (src.match(/^export function /gm) || []).length, 1,
    "exactly one function belongs here: the per-agent form of the reset invariant",
  );
  assert.doesNotMatch(src, /httpCall|fetch\(|fs\.|readAgents/, "no I/O and no other module's state");
  assert.doesNotMatch(src, /spawnTriggeredAgent|__markControllerStart|parseJson/, "out of scope for this owner");
});

// ---------------------------------------------------------------------------------------------------
// Interrupting everything in flight, appended to this module in a later v0.5.4 slice.
//
// It runs while the bridge is going down. The property that matters is that ONE controller misbehaving
// must not stop the others being told — `Promise.allSettled` plus a per-run try/catch, not `Promise.all`.

test("every active run is interrupted, with the reason passed through", async () => {
  ACTIVE_RUNS.clear();
  const seen = [];
  ACTIVE_RUNS.set("r1", { controller: { interrupt: async (why) => seen.push(["r1", why]) } });
  ACTIVE_RUNS.set("r2", { controller: { interrupt: async (why) => seen.push(["r2", why]) } });

  await interruptActiveRuns("Bridge shutdown");
  assert.deepEqual(seen.sort(), [["r1", "Bridge shutdown"], ["r2", "Bridge shutdown"]]);
});

test("one controller that THROWS does not stop the others being interrupted", async () => {
  // The whole reason for allSettled. With Promise.all the first rejection abandons the rest, and those
  // runs would be left believing they are still live while the bridge exits underneath them.
  ACTIVE_RUNS.clear();
  const reached = [];
  ACTIVE_RUNS.set("bad", { controller: { interrupt: async () => { throw new Error("gone"); } } });
  ACTIVE_RUNS.set("good", { controller: { interrupt: async () => reached.push("good") } });

  await interruptActiveRuns();
  assert.deepEqual(reached, ["good"]);
});

test("runs with no controller or no interrupt are skipped, not fatal", async () => {
  // A run claimed but not yet wired has no controller. Optional chaining is load-bearing here.
  ACTIVE_RUNS.clear();
  ACTIVE_RUNS.set("bare", {});
  ACTIVE_RUNS.set("noFn", { controller: {} });
  ACTIVE_RUNS.set("nullCtl", { controller: null });
  await interruptActiveRuns();
});

test("no active runs is a no-op that resolves", async () => {
  ACTIVE_RUNS.clear();
  await interruptActiveRuns();
  assert.equal(ACTIVE_RUNS.size, 0, "interrupting must not mutate the map — the runs clean themselves up");
});
