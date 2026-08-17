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

// ── stopRun ─────────────────────────────────────────────────────────────────
//
// Eighth cluster off the V8-coverage census: `stopRun` had a zero call count, and the fixture has answered
// POST /v1/runs/{id}/stop since the day it was written — the endpoint was modelled for a test nobody wrote.
//
// This is the CANCEL path. `comms_interrupt` against a hermes managed agent ends here, and a cancel that
// quietly fails leaves a run consuming the agent while the control plane believes it stopped it.

test("stopRun cancels a run and returns the status object", async (t) => {
  const { baseUrl, key } = await withFixture(t);
  const client = createHermesApiServerClient();
  const { runId } = await client.createRun({ baseUrl, key, input: "do a thing" });
  const res = await client.stopRun({ baseUrl, key, runId });
  assert.equal(res.status, "stopping");
  assert.equal(res.run_id, runId, "the stop landed on a different run than the one asked for");
});

test("stopRun refuses to POST without a runId", async () => {
  // Without this guard the URL becomes /v1/runs//stop, which is a 404 the caller would read as "the run is
  // already gone" — the most dangerous possible reading of a cancel that never happened.
  const client = createHermesApiServerClient();
  await assert.rejects(() => client.stopRun({ baseUrl: "http://127.0.0.2:1", key: "k" }),
    /stopRun requires a runId/);
});

test("stopRun percent-encodes a run id that contains a path separator", async (t) => {
  // A raw `/` in the id would silently retarget the request at a different path. The fixture's route matches
  // a single non-slash segment and decodes it, so a correctly-encoded id comes back verbatim.
  const { baseUrl, key } = await withFixture(t);
  const client = createHermesApiServerClient();
  const res = await client.stopRun({ baseUrl, key, runId: "run/with slash" });
  assert.equal(res.run_id, "run/with slash");
});

test("stopRun tolerates a baseUrl with a trailing slash", async (t) => {
  // The endpoint is assembled by concatenation, so an un-trimmed base produces `//v1/runs/...` — a different
  // path, and a 404 the caller cannot distinguish from a dead run.
  const { baseUrl, key } = await withFixture(t);
  const client = createHermesApiServerClient();
  const { runId } = await client.createRun({ baseUrl, key, input: "x" });
  const res = await client.stopRun({ baseUrl: `${baseUrl}/`, key, runId });
  assert.equal(res.status, "stopping");
});

test("stopRun surfaces a 401 as an auth error", async (t) => {
  const { baseUrl } = await withFixture(t);
  const client = createHermesApiServerClient();
  await assert.rejects(() => client.stopRun({ baseUrl, key: "wrong-key", runId: "run_x" }),
    (err) => {
      assert.match(String(err.message), /401|auth|key/i);
      return true;
    });
});

// The odd server answers below are not in the fixture's contract — a real gateway mid-restart is what
// produces them — so each stands up its own one-request server on 127.0.0.2.
async function withReplier(t, handler) {
  const http = await import("node:http");
  const server = http.createServer(handler);
  await new Promise((resolve) => server.listen(0, "127.0.0.2", resolve));
  t.after(() => new Promise((resolve) => server.close(() => resolve())));
  return `http://127.0.0.2:${server.address().port}`;
}

test("stopRun rejects on a non-auth failure and carries the server's body", async (t) => {
  // The body is the only diagnostic the operator gets. Dropping it leaves "stopRun failed (HTTP 500)".
  const baseUrl = await withReplier(t, (req, res) => {
    res.writeHead(500, { "Content-Type": "text/plain" });
    res.end("gateway is restarting");
  });
  const client = createHermesApiServerClient();
  await assert.rejects(() => client.stopRun({ baseUrl, key: "k", runId: "run_x" }), (err) => {
    assert.match(err.message, /HTTP 500/);
    assert.match(err.message, /gateway is restarting/);
    // AND IT MUST NOT BLAME THE KEY. Routing every failure through `authError` still reports the status and
    // the body, so both readings pass a looser assertion — while sending the operator to rotate a key that
    // was never the problem.
    assert.match(err.message, /stopRun failed/);
    assert.doesNotMatch(err.message, /auth failed/i);
    return true;
  });
});

test("stopRun treats a NON-JSON success as a stop that was accepted", async (t) => {
  // Some builds answer 200 with an empty body. Cancellation was still accepted, so the client reports
  // `stopping` rather than throwing a parse error at a caller that has nothing to retry.
  const baseUrl = await withReplier(t, (req, res) => {
    res.writeHead(200, { "Content-Type": "text/plain" });
    res.end("OK");
  });
  const client = createHermesApiServerClient();
  assert.deepEqual(await client.stopRun({ baseUrl, key: "k", runId: "run_x" }), { status: "stopping" });
});

test("stopRun sends its key as a Bearer header on POST", async (t) => {
  const seen = [];
  const baseUrl = await withReplier(t, (req, res) => {
    seen.push({ method: req.method, auth: req.headers.authorization, path: req.url });
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "stopping" }));
  });
  const client = createHermesApiServerClient();
  await client.stopRun({ baseUrl, key: "secret-key", runId: "run_x" });
  assert.deepEqual(seen, [
    { method: "POST", auth: "Bearer secret-key", path: "/v1/runs/run_x/stop" },
  ]);
});
