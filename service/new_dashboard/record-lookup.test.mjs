// Resolving a dashboard record by kind and id, tested by CALLING it.
//
// The JSON inspector receives a kind and an id from a data attribute and has to find the record again.
// Two properties carry the risk: an unknown kind must not throw (the attribute is written by many
// renderers), and messages are keyed differently from everything else.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import { lookup } from "./record-lookup.mjs";

const COLLECTIONS = ["agents", "contracts", "messages", "runs", "sessions", "environments"];

function withRecords(fields, run) {
  const saved = {};
  for (const k of COLLECTIONS) saved[k] = state[k];
  for (const k of COLLECTIONS) state[k] = [];
  Object.assign(state, fields);
  try { return run(); } finally { Object.assign(state, saved); }
}

test("every kind resolves into its own collection", () => {
  // Six kinds, six collections. A mis-wired pair would silently return the wrong record type, and the
  // inspector would show a session where the operator clicked a run.
  withRecords({
    agents: [{ id: "a1" }],
    contracts: [{ id: "c1" }],
    messages: [{ id: "m1" }],
    runs: [{ id: "r1" }],
    sessions: [{ id: "s1" }],
    environments: [{ id: "e1" }],
  }, () => {
    assert.equal(lookup("agent", "a1")?.id, "a1");
    assert.equal(lookup("contract", "c1")?.id, "c1");
    assert.equal(lookup("message", "m1")?.id, "m1");
    assert.equal(lookup("run", "r1")?.id, "r1");
    assert.equal(lookup("session", "s1")?.id, "s1");
    assert.equal(lookup("environment", "e1")?.id, "e1");
  });
});

test("a kind only ever searches its OWN collection", () => {
  // The complement: an id that exists under a different kind must not be found. Otherwise the inspector
  // would open whatever happened to share the id.
  withRecords({ agents: [{ id: "shared" }], runs: [] }, () => {
    assert.equal(lookup("agent", "shared")?.id, "shared");
    assert.equal(lookup("run", "shared"), undefined);
  });
});

test("MESSAGES MAY BE KEYED BY messageId INSTEAD OF id", () => {
  // `String(item.id || item.messageId)`. Message records arrive with either, and reading only `id` makes
  // the inspector fail to open exactly the records an operator clicks most.
  withRecords({ messages: [{ messageId: "m9", subject: "hi" }] }, () => {
    assert.equal(lookup("message", "m9")?.subject, "hi");
  });
});

test("ids are compared as STRINGS, so a numeric id still matches", () => {
  // Run ids arrive as numbers from some endpoints and as strings from the data attribute they are
  // rendered into. A strict comparison would never match those.
  withRecords({ runs: [{ id: 7 }] }, () => {
    assert.equal(lookup("run", "7")?.id, 7);
    assert.equal(lookup("run", 7)?.id, 7);
  });
});

test("an UNKNOWN KIND returns undefined rather than throwing", () => {
  // `(maps[kind] || [])`. Without the fallback this throws on any attribute the renderers have not been
  // taught about, and the throw happens inside a click handler — taking the rest of it with it.
  withRecords({ agents: [{ id: "a1" }] }, () => {
    assert.equal(lookup("no-such-kind", "a1"), undefined);
    assert.equal(lookup(undefined, "a1"), undefined);
    assert.equal(lookup("", "a1"), undefined);
  });
});

test("an EMPTY or absent collection is survived", () => {
  // Every collection is empty before the first refresh lands, which is when a deep-linked inspector
  // would try to resolve.
  withRecords({ agents: undefined }, () => {
    assert.doesNotThrow(() => lookup("agent", "a1"));
    assert.equal(lookup("agent", "a1"), undefined);
  });
});

test("a record with NEITHER id nor messageId does not match a blank id", () => {
  // Both sides stringify, so `String(undefined || undefined)` is "undefined" — asserting this pins that
  // a malformed record cannot be fetched by passing an empty id.
  withRecords({ runs: [{ subject: "no id" }] }, () => {
    assert.equal(lookup("run", ""), undefined);
    assert.equal(lookup("run", "undefined")?.subject, "no id", "…though the literal string does match");
  });
});
