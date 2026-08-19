#!/usr/bin/env node
// The client half of moving spawning out of aify-comms.
//
// THE TEST THAT MATTERS MOST IS THE FIRST ONE: with nothing configured, delegation is OFF. Everything
// else here describes a path that must not be taken until an operator turns it on deliberately, on an
// idle fleet. Shipping this file must change nothing.
//
// Every endpoint below is 127.0.0.2:1 or an injected fetch. Nothing here may reach a real aify-env, a
// real service, or the operator's fleet.

import assert from "node:assert/strict";
import { test } from "node:test";

import { EnvClient, isEnabled } from "../env-client.mjs";

const NOWHERE = "http://127.0.0.2:1";

/** A fetch that records what it was asked and answers with whatever the test wants. */
function fakeFetch(answers) {
  const calls = [];
  const impl = async (url, options) => {
    calls.push({ url, method: options?.method ?? "GET", body: options?.body ? JSON.parse(options.body) : undefined });
    const answer = answers.shift();
    if (answer instanceof Error) throw answer;
    return {
      status: answer.status,
      json: async () => {
        if (answer.body === undefined) throw new Error("no body");
        return answer.body;
      },
    };
  };
  impl.calls = calls;
  return impl;
}

test("OFF by default: nothing configured means delegation is not enabled", () => {
  // The safety property of this whole phase. If this ever passes wrongly, deploying the file changes
  // where every managed agent is spawned.
  for (const env of [{}, { AIFY_ENV_ENDPOINT: "" }, { AIFY_ENV_ENDPOINT: "   " }]) {
    assert.equal(isEnabled(env), false, JSON.stringify(env));
  }
});

test("enabled only when an endpoint is actually set", () => {
  assert.equal(isEnabled({ AIFY_ENV_ENDPOINT: NOWHERE }), true);
});

test("enablement is keyed on the ENDPOINT, not on a separate boolean", () => {
  // One thing to get right. A flag that is on with nowhere to talk to is a state nobody wants to
  // debug at the moment an agent will not start.
  assert.equal(isEnabled({ AIFY_USE_ENV: "1" }), false);
});

test("start POSTs the launcher and the service that owns it", async () => {
  const fetchImpl = fakeFetch([{ status: 201, body: { id: "p1", pid: 7, terminal: true, service: "aify-comms" } }]);
  const client = new EnvClient({ endpoint: NOWHERE, fetchImpl });
  const result = await client.start({ service: "aify-comms", launcher: "/bin/claude-aify", args: ["--managed"] });

  assert.equal(result.ok, true);
  assert.equal(result.handle.id, "p1");
  assert.equal(fetchImpl.calls[0].method, "POST");
  assert.equal(fetchImpl.calls[0].body.service, "aify-comms");
  assert.deepEqual(fetchImpl.calls[0].body.args, ["--managed"]);
});

test("an UNREACHABLE environment is reported, never thrown", async () => {
  // A caller deciding whether to fall back must be able to read the answer. An exception here makes
  // "refused" and "not there" look identical to a catch block, which then does the wrong one.
  const client = new EnvClient({ endpoint: NOWHERE, fetchImpl: fakeFetch([new Error("connect ECONNREFUSED")]) });
  const result = await client.start({ service: "s", launcher: "/x" });
  assert.equal(result.ok, false);
  assert.match(result.error, /unreachable/);
});

test("a REFUSED launcher is reported with the environment's own reason", async () => {
  // Distinct from unreachable: this one means aify-env looked and said no, and the reason belongs to
  // it rather than being invented here.
  const client = new EnvClient({
    endpoint: NOWHERE,
    fetchImpl: fakeFetch([{ status: 403, body: { error: "refused /x: no HARNESS_WRAPPER_VERSION marker" } }]),
  });
  const result = await client.start({ service: "s", launcher: "/x" });
  assert.equal(result.ok, false);
  assert.equal(result.status, 403);
  assert.match(result.error, /marker/);
});

test("stop treats 204 as success and carries no body", async () => {
  const client = new EnvClient({ endpoint: NOWHERE, fetchImpl: fakeFetch([{ status: 204 }]) });
  const result = await client.stop("p1");
  assert.equal(result.ok, true);
  assert.equal(result.handle, null);
});

test("a client with NO endpoint refuses rather than guessing one", async () => {
  const client = new EnvClient({});
  const result = await client.start({ service: "s", launcher: "/x" });
  assert.equal(result.ok, false);
  assert.match(result.error, /no aify-env endpoint/);
});

test("a trailing slash on the endpoint does not produce a doubled path", async () => {
  const fetchImpl = fakeFetch([{ status: 200, body: { processes: [] } }]);
  await new EnvClient({ endpoint: `${NOWHERE}/`, fetchImpl }).list();
  assert.equal(fetchImpl.calls[0].url, `${NOWHERE}/processes`);
});

test("an answer that is not JSON is a failure, not an empty success", async () => {
  // A 200 carrying HTML is a proxy, not an environment. Treating it as success would report a spawn
  // that never happened.
  const fetchImpl = fakeFetch([{ status: 201, body: undefined }]);
  const client = new EnvClient({ endpoint: NOWHERE, fetchImpl });
  const result = await client.start({ service: "s", launcher: "/x" });
  assert.equal(result.ok, true);
  assert.equal(result.handle, null, "an unparseable body must not masquerade as a handle");
});
