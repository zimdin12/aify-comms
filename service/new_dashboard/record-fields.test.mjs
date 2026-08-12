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
  contractCategory,
  environmentRoots,
  messageId,
  messageIdOf,
  messageRunId,
  runPendingControlCount,
  sessionEnvironmentId,
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

test("sessionEnvironmentId falls back to 'unassigned', not to empty", () => {
  // Sessions are GROUPED by this value, so '' would create an unnamed bucket; 'unassigned' is a real
  // group the UI renders deliberately.
  assert.equal(sessionEnvironmentId({}), "unassigned");
  assert.equal(sessionEnvironmentId(null), "unassigned");
  for (const key of ["environmentId", "environment_id", "envId", "env_id"]) {
    assert.equal(sessionEnvironmentId({ [key]: "env-1" }), "env-1", `${key} must be recognised`);
  }
});

test("sessionRuntime falls back to the literal 'runtime'", () => {
  assert.equal(sessionRuntime({}), "runtime");
  assert.equal(sessionRuntime(null), "runtime");
  for (const key of ["runtime", "runtimeKind", "kind"]) {
    assert.equal(sessionRuntime({ [key]: "claude-code" }), "claude-code", `${key} must be recognised`);
  }
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
