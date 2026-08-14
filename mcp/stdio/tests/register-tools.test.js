// Every MCP tool the bridge exposes is registered here, and this counts them.
//
// Extracted from server.js in v0.5.4. The registration list IS the bridge's public surface: an agent
// can call exactly what is registered and nothing else. A tool dropped from the list does not fail
// loudly — it simply stops existing, and the agent that reaches for it gets "unknown tool" from the MCP
// layer with no hint that it ever worked. Nothing else in the repo would notice.
//
// So this CALLS the registration with a recording double and asserts what got registered, rather than
// reading the file and hoping it looks right.

import assert from "node:assert/strict";
import test from "node:test";

import { registerAllTools } from "../register-tools.mjs";

/** An McpServer double that records every registration, however the SDK's API is called. */
function recordingServer() {
  const registered = [];
  const record = (name) => { if (typeof name === "string") registered.push(name); };
  const handler = {
    get(_t, prop) {
      if (prop === "__registered") return registered;
      // Any method the registrars reach for records its first string argument — the tool name — and
      // returns the same proxy so chained calls keep working.
      return (...args) => { record(args[0]); return proxy; };
    },
  };
  const proxy = new Proxy({}, handler);
  return proxy;
}

/** A zod stand-in: every accessor returns a chainable no-op. */
function fakeZ() {
  const chain = new Proxy(function () {}, {
    get: () => chain,
    apply: () => chain,
  });
  return chain;
}

function registerOnce() {
  const server = recordingServer();
  let dispatchStarted = 0;
  registerAllTools(server, fakeZ(), { ensureDispatchLoop: () => { dispatchStarted += 1; } });
  return { names: server.__registered, dispatchStarted };
}

test("registering succeeds against a bare double — nothing reaches outside the call", () => {
  // If a registrar did work at registration time rather than at call time, this would fail here. That
  // is worth knowing: registration runs at bridge start, before anything is connected.
  assert.doesNotThrow(() => registerOnce());
});

test("THE FULL TOOL SURFACE IS REGISTERED — counted, not eyeballed", () => {
  // The number is the point. A registrar deleted, or a call commented out during debugging, drops tools
  // silently; agents then get "unknown tool" for something that worked yesterday.
  // MEASURED AT 34 when this was written. The floor is set just under it rather than at a round number
  // a long way below: `>= 20` would have let FOURTEEN tools disappear while still passing, which is the
  // failure mode this test exists to catch, not a margin to leave open.
  const { names } = registerOnce();
  assert.ok(names.length >= 32,
    `expected the full tool surface (34 when written), got ${names.length}: ${names.join(", ")}`);
});

test("the tools agents actually depend on are present by NAME", () => {
  // Named individually because these are the ones the skills, the docs and every agent's habits assume.
  // A count alone would let one be swapped for another and still pass.
  const { names } = registerOnce();
  for (const tool of [
    "comms_register",
    "comms_send",
    "comms_inbox",
    "comms_read",
    "comms_agents",
    "comms_dispatch",
    "comms_status",
  ]) {
    assert.ok(names.includes(tool), `${tool} must be registered — agents call it by this exact name`);
  }
});

test("no tool is registered TWICE", () => {
  // A duplicate is not harmless: the second registration silently wins, so a stale handler can shadow
  // the live one and the only symptom is a tool behaving like an older version of itself.
  const { names } = registerOnce();
  const seen = new Set();
  const dupes = names.filter((n) => (seen.has(n) ? true : (seen.add(n), false)));
  assert.deepEqual(dupes, [], "duplicate tool registrations");
});

test("registration does NOT start the dispatch loop", () => {
  // `ensureDispatchLoop` is passed through to the registration tool so a NEW registration can start
  // dispatch. Calling it during setup would arm the loop before the bridge has an identity — the state
  // the v0.5.4 shutdown-gate work showed claims work nobody can execute.
  const { dispatchStarted } = registerOnce();
  assert.equal(dispatchStarted, 0, "the loop must start on registration, not on setup");
});
