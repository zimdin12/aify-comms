// `session-handles` — two agents claiming one conversation.
//
// BOTH FIXTURES BELOW ARE REAL, taken off the operator's fleet on 2026-08-31, and between them they
// are the two session failures that cost that day:
//
//   * `651b895f-…` held by comms-claude AND comms-tech-lead. One Claude Code session re-registered
//     under a new id; the old row kept the handle with nothing heartbeating for it. It read `offline`
//     for ever while its `lastSeen` refreshed on every tool call, and every review verdict sent to it
//     was refused and relayed. Hours were spent restarting a reviewer that was not the broken part.
//   * `20260715_…` held by four hermes agents. Four agents appending to one conversation is how that
//     thread reached 1.1M tokens against a 900k window.
//
// Neither was visible from any status badge, and neither is findable one agent at a time -- which is
// the argument for a check that takes the whole population.

import assert from "node:assert/strict";
import test from "node:test";

import {
  checkSessionHandles,
  duplicateSessionHandles,
  sessionHandleVerdict,
} from "../session-handle-check.mjs";

const RESIDENT = "651b895f-a564-4d3a-8e0b-27f8429b1dd0";
const HERMES = "20260715_001441_960b8f";

/** The live fleet's shape, trimmed to what this check reads. */
const FLEET = {
  "comms-claude": { sessionHandle: RESIDENT },
  "comms-tech-lead": { sessionHandle: RESIDENT },
  "mc-senior-dev": { sessionHandle: HERMES },
  "guns-ab-planner": { sessionHandle: HERMES },
  "safety-gate-auditor": { sessionHandle: HERMES },
  "pathweaver-activation-auditor": { sessionHandle: HERMES },
  "sc-tester": { sessionHandle: "20260701_015609_630430" },
  "apg-pilot-01": { sessionHandle: "" },
  "apg-pilot-02": {},
};

// ── duplicateSessionHandles ─────────────────────────────────────────────────────────────────────

test("it finds both real collisions and leaves the singly-held session alone", () => {
  const dupes = duplicateSessionHandles(FLEET);
  assert.equal(dupes.length, 2);
  assert.deepEqual(dupes.map((d) => d.handle), [HERMES, RESIDENT], "widest collision is not first");
  assert.deepEqual(dupes[1].agentIds, ["comms-claude", "comms-tech-lead"]);
  assert.ok(!dupes.some((d) => d.agentIds.includes("sc-tester")),
            "an agent holding its own handle was reported as colliding");
});

test("EMPTY handles are not a collision, however many agents have none", () => {
  // Most of the fleet has no handle -- never bound, or managed and waiting to cold-start. Grouping
  // those would report the healthy majority as one enormous collision and bury the real two.
  const dupes = duplicateSessionHandles({
    a: { sessionHandle: "" },
    b: { sessionHandle: "   " },
    c: {},
    d: { sessionHandle: null },
  });
  assert.deepEqual(dupes, []);
});

test("the agents in a collision are sorted, so the row does not churn between runs", () => {
  const dupes = duplicateSessionHandles({ zeta: { sessionHandle: "h" }, alpha: { sessionHandle: "h" } });
  assert.deepEqual(dupes[0].agentIds, ["alpha", "zeta"]);
});

test("a handle is compared TRIMMED, so whitespace cannot hide a collision", () => {
  const dupes = duplicateSessionHandles({ a: { sessionHandle: "h" }, b: { sessionHandle: " h " } });
  assert.equal(dupes.length, 1, "a padded handle was treated as a different session");
});

test("junk in the listing is skipped rather than crashing the doctor", () => {
  const dupes = duplicateSessionHandles({ a: null, b: "not an object", c: { sessionHandle: "h" } });
  assert.deepEqual(dupes, []);
});

// ── sessionHandleVerdict ────────────────────────────────────────────────────────────────────────

test("a clean fleet passes and says how many it actually compared", () => {
  // "34 agents, each claimed once" is a different statement from "nothing found", and only the first
  // tells the operator the check had anything to look at.
  const verdict = sessionHandleVerdict([], { measured: 34 });
  assert.equal(verdict.ok, true);
  assert.match(verdict.detail, /34 agent\(s\) carry a session handle/);
});

test("a fleet where NOBODY holds a handle says so rather than claiming a clean result", () => {
  assert.match(sessionHandleVerdict([], { measured: 0 }).detail, /no agent carries a session handle/);
});

test("collisions FAIL and name every agent involved", () => {
  const verdict = sessionHandleVerdict(duplicateSessionHandles(FLEET), { measured: 7 });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "shared");
  for (const id of ["comms-claude", "comms-tech-lead", "mc-senior-dev", "guns-ab-planner"]) {
    assert.ok(verdict.detail.includes(id), `${id} was not named`);
  }
  assert.match(verdict.detail, /2 session\(s\)/);
  assert.match(verdict.detail, /6 agents involved/);
});

test("the fix names BOTH shapes, because they call for opposite actions", () => {
  // A resident collision means one row is a ghost and messages to it are being relayed; a managed one
  // means two agents are filling one context window. Telling the operator only one of those sends
  // half of them looking in the wrong place.
  const verdict = sessionHandleVerdict(duplicateSessionHandles(FLEET), { measured: 7 });
  assert.match(verdict.fix, /ghost/, "the resident case is not explained");
  assert.match(verdict.fix, /binding file/, "it does not say where the real binding is read");
  assert.match(verdict.fix, /context-window/, "it does not point at the managed consequence");
});

// ── the CHECK, not just its verdict ─────────────────────────────────────────────────────────────

function harness(listing) {
  const calls = { added: [], fetched: [] };
  return {
    calls,
    deps: {
      get: async (path) => { calls.fetched.push(path); return listing; },
      add: (...args) => { calls.added.push(args); return args; },
    },
  };
}

test("the check reads the fleet and reports the collisions", () => {
  const { deps, calls } = harness({ agents: FLEET });
  return checkSessionHandles(deps).then(() => {
    const [id, ok, code, detail] = calls.added[0];
    assert.equal(id, "session-handles");
    assert.equal(ok, false);
    assert.equal(code, "shared");
    assert.match(detail, /comms-claude/);
  });
});

test("a clean fleet passes through the CHECK, not just the predicate", () => {
  // Anti-vacuity: a check hard-wired to fail would satisfy the test above.
  const { deps, calls } = harness({ agents: { a: { sessionHandle: "x" }, b: { sessionHandle: "y" } } });
  return checkSessionHandles(deps).then(() => {
    assert.equal(calls.added[0][1], true);
    assert.equal(calls.added[0][2], "ok");
  });
});

test("a service that does not answer is UNKNOWN, never a pass", () => {
  // No evidence is not a pass. A green row for a listing nobody could read is indistinguishable from
  // a fleet with no collisions, and this repo has shipped that false green twice before.
  const { deps, calls } = harness(null);
  return checkSessionHandles(deps).then(() => {
    const [, ok, code] = calls.added[0];
    assert.equal(ok, false);
    assert.equal(code, "unknown");
  });
});

test("a listing whose `agents` is not an object is unknown too, not empty", () => {
  const { deps, calls } = harness({ agents: [] });
  return checkSessionHandles(deps).then(() => {
    // An array has typeof "object", so it reaches the counter rather than the guard -- and yields a
    // legitimate "nobody holds a handle". What must NOT happen is a crash or a silent wrong answer.
    assert.equal(calls.added[0][0], "session-handles");
  });
});

test("it costs ONE read, so it can run on every doctor invocation", () => {
  const { deps, calls } = harness({ agents: FLEET });
  return checkSessionHandles(deps).then(() => {
    assert.deepEqual(calls.fetched, ["/api/v1/agents"]);
  });
});

test("an ARRAY is not an agent map — it is UNKNOWN, not a clean fleet", () => {
  // REVIEWER FINDING, 2026-08-31, and the sharp half is that the test above this one had already
  // noticed an array reaches the counter and merely asserted it did not crash. `typeof [] ===
  // "object"`, so a bare typeof guard lets it through, and an array yields no entries in the shape
  // this check wants -- which renders as "nobody holds a handle". Documenting a hole is not closing
  // it.
  const { deps, calls } = harness({ agents: [] });
  return checkSessionHandles(deps).then(() => {
    const [, ok, code] = calls.added[0];
    assert.equal(ok, false, "an array listing was reported as a clean fleet");
    assert.equal(code, "unknown");
  });
});

test("a plain object listing is still accepted — the guard did not become paranoid", () => {
  const { deps, calls } = harness({ agents: { a: { sessionHandle: "x" } } });
  return checkSessionHandles(deps).then(() => {
    assert.equal(calls.added[0][1], true);
  });
});
