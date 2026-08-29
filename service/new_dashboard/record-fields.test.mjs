// Unit tests for the API-record field readers.
//
// The defect these functions exist to prevent is invisible in a passing render: a missing id does not
// throw, it produces an empty string, an unclickable row, or a filter that matches nothing, and the page
// still draws. So the useful assertions here are the ALIAS cases (does it find the field under each name
// the API actually uses) and the DEGENERATE cases (what does it return when the field is absent entirely).
//
// Every alias below is one the production code lists, so each test is really asking: was that alias ever
// correct, and does it still work? A source-reading test can confirm the alias appears in the file. Only
// calling the function can confirm it is reached.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  asAgentArray,
  asArray,
  contractActionable,
  contractCategory,
  environmentRoots,
  messageId,
  messageIdOf,
  messageRunId,
  runPendingControlCount,
  runTargetAgent,
  sessionAgentId,
  sessionEnvironmentId,
  sessionId,
  sessionRuntime,
} from "./record-fields.mjs";

test("messageIdOf finds the id under each name the API uses", () => {
  assert.equal(messageIdOf({ id: "a" }), "a");
  assert.equal(messageIdOf({ messageId: "b" }), "b");
  assert.equal(messageIdOf({ message_id: "c" }), "c");
});

test("messageIdOf prefers id, then messageId, then message_id", () => {
  // Precedence is behaviour: a payload carrying two of them must resolve deterministically.
  assert.equal(messageIdOf({ id: "a", messageId: "b", message_id: "c" }), "a");
  assert.equal(messageIdOf({ messageId: "b", message_id: "c" }), "b");
});

test("messageIdOf coerces a numeric id to a string", () => {
  // Ids are compared with === against DOM data-* attributes, which are always strings.
  assert.equal(messageIdOf({ id: 42 }), "42");
  assert.strictEqual(typeof messageIdOf({ id: 42 }), "string");
});

test("messageIdOf returns empty for a missing, null or empty record", () => {
  for (const record of [null, undefined, {}, { other: "x" }]) {
    assert.equal(messageIdOf(record), "", `unexpected for ${JSON.stringify(record)}`);
  }
});

test("messageId delegates to messageIdOf", () => {
  assert.equal(messageId({ message_id: "z" }), messageIdOf({ message_id: "z" }));
  assert.equal(messageId(null), "");
});

test("messageRunId searches all six run-id aliases", () => {
  const aliases = [
    "dispatchRunId", "dispatch_run_id", "runId", "run_id", "contractRunId", "contract_run_id",
  ];
  for (const key of aliases) {
    assert.equal(messageRunId({ [key]: "r1" }), "r1", `${key} must be recognised`);
  }
});

test("messageRunId returns empty when a message belongs to no run", () => {
  // '' means "not part of a run", which the UI treats differently from a run that exists but is unknown.
  for (const record of [null, undefined, {}, { id: "m1" }]) assert.equal(messageRunId(record), "");
});

// WHAT THE TWO SENTINELS COST, so the next person to add a display default to a data reader has the
// list rather than the principle. `sessionEnvironmentId` answered 'unassigned' and `sessionRuntime`
// answered 'runtime' for a record that carried neither, and a truthy string is indistinguishable from
// a real value at every call site:
//
//   - `statusWhyContext` guards each optional tooltip line with `if (sessionEnvironmentId(item))`.
//     Neither guard could be false, so a session with no binding explained itself as
//     "Environment: unassigned. Runtime: runtime."
//   - `agent-drawer` renders `sessionEnvironmentId(session) || '—'` and printed the word: the dash
//     was unreachable.
//   - `identity-directory` had to UNDO it (`envLabel === 'unassigned' ? '—' : …`), which is the tell.
//     That undo also matched a session whose environment_id was genuinely the string `unassigned`,
//     because the sentinel and the column value were spelled the same.
//   - `session-console` printed `runtime · unassigned` in its meta line.
//   - The Continue/Compact form pre-filled both inputs from these readers and POSTed
//     `v('cont-env') || sessionEnvironmentId(target)`, so a session with no binding sent
//     `environmentId: "unassigned"` to /spawn-requests and the operator was told
//     `Environment "unassigned" not found` -- an environment that has never existed on any host.
//
// The rail is the ONE caller that needs a word, because it uses the value as a group heading. It
// says `|| 'unassigned'` at the point where the group is named.
test("sessionEnvironmentId answers empty for a session with no binding, not a word", () => {
  // The grouping argument this test used to carry -- "sessions are GROUPED by this value, so ''
  // would create an unnamed bucket" -- was true about the RAIL and wrong about the reader. The rail
  // names its own empty group; every other caller was handed a truthy string it could not tell from
  // a real environment id, and one of them posted it to /spawn-requests.
  assert.equal(sessionEnvironmentId({}), "");
  assert.equal(sessionEnvironmentId(null), "");
  for (const key of ["environmentId", "environment_id", "envId", "env_id"]) {
    assert.equal(sessionEnvironmentId({ [key]: "env-1" }), "env-1", `${key} must be recognised`);
  }
});

test("sessionRuntime answers empty for a session that names none", () => {
  assert.equal(sessionRuntime({}), "");
  assert.equal(sessionRuntime(null), "");
  for (const key of ["runtime", "runtimeKind", "kind"]) {
    assert.equal(sessionRuntime({ [key]: "claude-code" }), "claude-code", `${key} must be recognised`);
  }
});

test("a guard on either reader can actually be false", () => {
  // The narrowest statement of the defect. `statusWhyContext` writes
  // `if (sessionEnvironmentId(item)) parts.push(...)`, and with a sentinel that condition was true
  // for every session ever passed to it -- a guard that cannot fail is decoration, and this one
  // decorated a tooltip with "Environment: unassigned. Runtime: runtime."
  assert.ok(!sessionEnvironmentId({ agentId: "a1" }), "the environment guard is unreachable");
  assert.ok(!sessionRuntime({ agentId: "a1" }), "the runtime guard is unreachable");
});

test("runPendingControlCount counts only pending and claimed controls", () => {
  const run = {
    controls: [
      { status: "pending" }, { status: "claimed" }, { status: "completed" },
      { status: "failed" }, { status: "cancelled" },
    ],
  };
  assert.equal(runPendingControlCount(run), 2);
});

test("runPendingControlCount is case-insensitive and tolerates a missing status", () => {
  assert.equal(runPendingControlCount({ controls: [{ status: "PENDING" }, { status: "Claimed" }] }), 2);
  assert.equal(runPendingControlCount({ controls: [{}, { status: null }] }), 0);
});

test("runPendingControlCount returns 0 for a run with no controls at all", () => {
  for (const run of [null, undefined, {}, { controls: [] }]) {
    assert.equal(runPendingControlCount(run), 0, `unexpected for ${JSON.stringify(run)}`);
  }
});

test("contractCategory prefers explicit category, then kind, then infers from flags", () => {
  assert.equal(contractCategory({ category: "Direct" }), "direct", "the result is lowercased");
  assert.equal(contractCategory({ kind: "CHANNEL" }), "channel");
  assert.equal(contractCategory({ channel: true }), "channel", "inferred from the channel flag");
  assert.equal(contractCategory({ selfWake: true }), "self_wake");
  assert.equal(contractCategory({ self_wake: true }), "self_wake", "snake_case flag too");
  assert.equal(contractCategory({}), "direct", "the default when nothing is set");
});

test("asAgentArray accepts both the array and the id-keyed-object payload shapes", () => {
  const asArray = asAgentArray({ agents: [{ id: "a1" }, { id: "a2" }] });
  assert.deepEqual(asArray.map((a) => a.id), ["a1", "a2"]);

  // The map form: the KEY becomes the id, which is the whole reason this function exists.
  const asMap = asAgentArray({ agents: { a1: { role: "coder" }, a2: { role: "tester" } } });
  assert.deepEqual(asMap, [{ id: "a1", role: "coder" }, { id: "a2", role: "tester" }]);
});

test("asAgentArray returns an empty array when the payload has no agents", () => {
  // The callers iterate the result directly, so returning undefined here would throw at the call site.
  assert.deepEqual(asAgentArray({}), []);
  assert.deepEqual(asAgentArray({ agents: null }), []);
});

test("environmentRoots searches four aliases and filters empty entries", () => {
  for (const key of ["cwdRoots", "cwd_roots", "roots", "workspaceRoots"]) {
    assert.deepEqual(environmentRoots({ [key]: ["/a"] }), ["/a"], `${key} must be recognised`);
  }
  assert.deepEqual(environmentRoots({ cwdRoots: ["/a", "", null, "/b"] }), ["/a", "/b"]);
});

test("environmentRoots returns an empty array for a missing or non-array value", () => {
  for (const env of [null, undefined, {}, { cwdRoots: "not-an-array" }, { cwdRoots: 7 }]) {
    assert.deepEqual(environmentRoots(env), [], `unexpected for ${JSON.stringify(env)}`);
  }
});

// ── the last three readers of this shape, moved in v0.5.4 ────────────────────────────────────────
//
// Each exists because the API spells the same field more than one way — camelCase from the newer routes,
// snake_case from the older ones, and a bare short name in places. A reader that missed a spelling does not
// throw; it returns "" and the row silently loses its identity, which is how a session stops matching its
// own agent.

test("sessionId accepts every spelling the API uses, in priority order", () => {
  assert.equal(sessionId({ id: "a", sessionId: "b", session_id: "c" }), "a", "id wins");
  assert.equal(sessionId({ sessionId: "b", session_id: "c" }), "b", "…then sessionId");
  assert.equal(sessionId({ session_id: "c" }), "c", "…then session_id");
});

test("sessionAgentId accepts every spelling, including the bare `agent`", () => {
  assert.equal(sessionAgentId({ agentId: "a", agent_id: "b", agent: "c" }), "a");
  assert.equal(sessionAgentId({ agent_id: "b", agent: "c" }), "b");
  assert.equal(sessionAgentId({ agent: "c" }), "c", "the short form is a real spelling, not a fallback");
});

test("runTargetAgent accepts every spelling of a run's target", () => {
  // Four spellings, and this one matters most: `sessionForRun` resolves a run to a session through it, so a
  // missed spelling detaches a running turn from the session showing it.
  assert.equal(runTargetAgent({ targetAgentId: "a", target_agent: "b", agentId: "c", agent_id: "d" }), "a");
  assert.equal(runTargetAgent({ target_agent: "b", agentId: "c" }), "b");
  assert.equal(runTargetAgent({ agentId: "c", agent_id: "d" }), "c");
  assert.equal(runTargetAgent({ agent_id: "d" }), "d");
});

test("all three answer EMPTY rather than undefined for a missing or junk record", () => {
  // Callers compare these with `===` against other ids. `undefined` would compare unequal to everything and
  // read as "no match" rather than "no data", which is the same outcome by accident rather than by design —
  // and `String(undefined)` would produce the literal "undefined", which matches nothing but looks like an id.
  for (const read of [sessionId, sessionAgentId, runTargetAgent]) {
    for (const record of [undefined, null, {}, { unrelated: 1 }, ""]) {
      const out = read(record);
      assert.equal(typeof out, "string", `${read.name}(${JSON.stringify(record)}) must be a string`);
      assert.equal(out, "", `${read.name}(${JSON.stringify(record)}) must be empty, not "${out}"`);
    }
  }
});

test("they coerce to string, so a numeric id still compares", () => {
  // The API has returned numeric ids; every call site compares with `===` against a string.
  assert.equal(sessionId({ id: 42 }), "42");
  assert.equal(sessionAgentId({ agentId: 7 }), "7");
  assert.equal(runTargetAgent({ targetAgentId: 9 }), "9");
});

test("a falsy-but-present value falls through rather than winning", () => {
  // Current behaviour, pinned: these use `||`, so an empty string under the preferred name yields to the
  // next spelling instead of returning "". That is what makes a partially-populated record still resolve.
  assert.equal(sessionId({ id: "", sessionId: "b" }), "b");
  assert.equal(sessionAgentId({ agentId: "", agent: "c" }), "c");
  assert.equal(runTargetAgent({ targetAgentId: "", agent_id: "d" }), "d");
});

// ── asArray / contractActionable, moved from app.js in v0.5.4 ────────────────────────────────────

test("asArray accepts BOTH shapes the API returns for a collection", () => {
  // Some routes send a list, others a map keyed by id. A caller that assumed one shape would render an
  // empty page against the other — with no error, because `.map` on `{}` never runs.
  assert.deepEqual(asArray({ items: [{ id: "a" }] }, "items"), [{ id: "a" }], "a list passes through");
  assert.deepEqual(asArray({ items: { a: { n: 1 } } }, "items"), [{ id: "a", n: 1 }],
    "a map becomes a list, with the key promoted to `id`");
});

test("asArray gives an empty list rather than undefined for anything else", () => {
  // Callers immediately `.map`/`.filter` the result; `undefined` would throw inside a render.
  for (const payload of [undefined, null, {}, { items: null }, { items: "text" }, { items: 7 }]) {
    assert.deepEqual(asArray(payload, "items"), [], `${JSON.stringify(payload)} must yield []`);
  }
});

test("asArray does not lose a map entry's own id", () => {
  // The key is the id, but an entry may also carry one. Current behaviour: the entry's own value wins,
  // because it is spread AFTER the key.
  assert.deepEqual(asArray({ m: { k: { id: "inner" } } }, "m"), [{ id: "inner" }]);
});

test("contractActionable is TRUE only for a contract someone can actually act on", () => {
  // It gates an action button. A false positive offers an action that fails; a false negative hides work
  // an operator owes.
  const open = { id: "c1", targetAgentId: "agent-a", state: "open" };
  assert.equal(contractActionable(open), true);
  assert.equal(contractActionable({ ...open, state: "answered" }), false, "answered is done");
  assert.equal(contractActionable({ ...open, state: "closed" }), false, "closed is done");
  assert.equal(contractActionable({ ...open, state: "CLOSED" }), false, "…case-insensitively");
  assert.equal(contractActionable({ ...open, targetAgentId: "dashboard" }), false,
    "a contract targeting the dashboard has no agent to act");
  assert.equal(contractActionable({ ...open, targetAgentId: "  " }), false, "…nor does a blank target");
  assert.equal(contractActionable({ ...open, id: "" }), false, "…nor one with no id to act on");
});

test("contractActionable never throws on a missing record", () => {
  for (const c of [undefined, null, {}, { id: "x" }]) {
    assert.equal(contractActionable(c), false, `${JSON.stringify(c)} must be inactionable, not an error`);
  }
});
