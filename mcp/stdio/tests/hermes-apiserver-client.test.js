#!/usr/bin/env node
// Unit tests for the hermes api_server HTTP/SSE client against the in-process
// fake-hermes-apiserver fixture. Shapes mirror
// docs/superpowers/specs/2026-05-30-hermes-apiserver-contract.md.

import assert from "node:assert/strict";
import { test } from "node:test";
import { createHermesApiServerClient } from "../hermes-apiserver-client.js";
import { start } from "./fixtures/fake-hermes-apiserver.mjs";

async function withFixture(t, opts) {
  const fixture = await start(opts);
  t.after(() => fixture.close());
  return fixture;
}

test("health returns ok against the fixture (no auth)", async (t) => {
  const { baseUrl } = await withFixture(t);
  const client = createHermesApiServerClient();
  const res = await client.health({ baseUrl });
  assert.equal(res.ok, true);
  assert.equal(res.status, "ok");
});

test("ensureSession resolves on 201 for a new id", async (t) => {
  const { baseUrl, key } = await withFixture(t);
  const client = createHermesApiServerClient();
  await assert.doesNotReject(() =>
    client.ensureSession({ baseUrl, key, id: "aify-new-agent" }),
  );
});

test("ensureSession resolves on 409 for a pre-seeded id (idempotent)", async (t) => {
  const { baseUrl, key } = await withFixture(t, { seedSessionIds: ["already-exists"] });
  const client = createHermesApiServerClient();
  await assert.doesNotReject(() =>
    client.ensureSession({ baseUrl, key, id: "already-exists" }),
  );
});

test("ensureSession rejects with a clear error on 401 (bad key)", async (t) => {
  const { baseUrl } = await withFixture(t);
  const client = createHermesApiServerClient();
  await assert.rejects(
    () => client.ensureSession({ baseUrl, key: "wrong-key", id: "x" }),
    (err) => {
      assert.match(String(err.message), /401|api key|unauthor/i);
      return true;
    },
  );
});

test("chatStream sends Bearer + X-Hermes-Session-Id, streams deltas, resolves full text", async (t) => {
  const { baseUrl, key } = await withFixture(t);
  const client = createHermesApiServerClient();
  await client.ensureSession({ baseUrl, key, id: "aify-chat" });

  const deltas = [];
  const reply = await client.chatStream({
    baseUrl,
    key,
    sessionId: "aify-chat",
    text: "hello",
    onDelta: (chunk) => deltas.push(chunk),
  });

  // Both assistant.delta chunks observed via onDelta.
  assert.equal(deltas.length, 2);
  assert.equal(deltas.join(""), "echo:hello");
  // Resolves with assistant.completed.content (authoritative full body).
  assert.equal(reply, "echo:hello");
});

test("chatStream rejects with a clear error on 401 (bad key)", async (t) => {
  const { baseUrl } = await withFixture(t);
  const client = createHermesApiServerClient();
  await assert.rejects(
    () =>
      client.chatStream({
        baseUrl,
        key: "wrong-key",
        sessionId: "already-exists",
        text: "hi",
        onDelta: () => {},
      }),
    (err) => {
      assert.match(String(err.message), /401|api key|unauthor/i);
      return true;
    },
  );
});

test("runEvents parses data:-only frames to the terminal run.completed", async (t) => {
  const { baseUrl, key } = await withFixture(t);
  const client = createHermesApiServerClient();
  const { runId } = await client.createRun({ baseUrl, key, input: "do a thing" });
  assert.ok(runId);

  const deltas = [];
  const result = await client.runEvents({
    baseUrl,
    key,
    runId,
    onDelta: (chunk) => deltas.push(chunk),
  });

  assert.equal(deltas.join(""), "hi there");
  assert.equal(result.status, "completed");
  assert.equal(result.output, "hi there");
});
