// The aify service HTTP client's actual request — headers, URL, failures, and the deadline.
//
// Twentieth cluster off the V8-coverage census: `aify-http.mjs`'s inner `httpCall`. The FACTORY
// (`makeAifyHttpCall`) is called by both hermes delivery paths, but the closure it returns had a zero call
// count: nothing had ever sent a request through it.
//
// WHAT IT CARRIES. Both hermes delivery paths — `runDeliveryLoop` and `startResumeMarkerSync` — reach the aify
// service through this one function. Its own header states the reason it exists: "a bridge that hangs on a
// request to a service that is down stops delivering work and reports nothing, which is indistinguishable from
// an idle agent." That deadline is the claim most worth testing, and it was the only one with no test at all.
//
// A REAL SERVER on 127.0.0.2, not a stubbed `fetch`. The subject is what goes onto the wire — the URL shape,
// the API-key header, the JSON body — and a stub would only prove what the test itself constructed.
//
// TIMEOUT SEALING: `HTTP_TIMEOUT_MS` is resolved ONCE at module load from AIFY_HTTP_TIMEOUT_MS (floored at
// 1000ms), so the deadline case sets it BEFORE a dynamic import and asserts the value it got. The default is
// 20s; a test that waited for that would be indistinguishable from a hang.

import assert from "node:assert/strict";
import test from "node:test";
import http from "node:http";

// Deliberately BEFORE the import below: the module freezes its timeout at load.
process.env.AIFY_HTTP_TIMEOUT_MS = "1000";
const { makeAifyHttpCall } = await import("../aify-http.mjs");

// One server per case, recording what it received.
async function withServer(handler, run) {
  const received = [];
  const server = http.createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    received.push({
      method: req.method,
      url: req.url,
      headers: req.headers,
      body: Buffer.concat(chunks).toString("utf8"),
    });
    handler(req, res);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.2", resolve));
  const baseUrl = `http://127.0.0.2:${server.address().port}`;
  try {
    return await run(baseUrl, received);
  } finally {
    server.closeAllConnections?.();
    await new Promise((resolve) => server.close(() => resolve()));
  }
}

const json = (res, status, payload) => {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(payload));
};

// ── not configured ──────────────────────────────────────────────────────────

test("with no base url, nothing is sent and null comes back", async () => {
  // A bridge with no service configured must not attempt a request at all. Returning null (rather than
  // throwing) is what lets the delivery loops treat "not configured" as a quiet no-op instead of an error they
  // would log on every tick.
  for (const baseUrl of ["", null, undefined]) {
    const call = makeAifyHttpCall(baseUrl, "key");
    assert.equal(await call("GET", "/agents"), null, `${JSON.stringify(baseUrl)} was not treated as unset`);
  }
});

// ── the wire ────────────────────────────────────────────────────────────────

test("the endpoint is placed under /api/v1 and the key travels as X-API-Key", async () => {
  await withServer((req, res) => json(res, 200, { ok: true }), async (baseUrl, received) => {
    const call = makeAifyHttpCall(baseUrl, "secret-key");
    assert.deepEqual(await call("GET", "/agents/one"), { ok: true });

    assert.equal(received.length, 1);
    assert.equal(received[0].method, "GET");
    assert.equal(received[0].url, "/api/v1/agents/one", "the endpoint is not mounted under /api/v1");
    assert.equal(received[0].headers["x-api-key"], "secret-key");
    assert.equal(received[0].headers["content-type"], undefined,
      "a bodyless request declared a content type");
    assert.equal(received[0].body, "");
  });
});

test("no api key means no key header at all", async () => {
  // An empty `X-API-Key` is not the same as none: a service configured to require a key would reject the
  // request rather than fall through to whatever unauthenticated handling exists.
  await withServer((req, res) => json(res, 200, {}), async (baseUrl, received) => {
    await makeAifyHttpCall(baseUrl, "")("GET", "/health");
    assert.equal("x-api-key" in received[0].headers, false,
      `a key header was sent anyway: ${JSON.stringify(received[0].headers["x-api-key"])}`);
  });
});

test("a body is sent as JSON, with the content type that goes with it", async () => {
  await withServer((req, res) => json(res, 200, { stored: true }), async (baseUrl, received) => {
    const call = makeAifyHttpCall(baseUrl, "k");
    await call("POST", "/messages/send", { to: "agent-a", body: "hello" });

    assert.equal(received[0].method, "POST");
    assert.equal(received[0].headers["content-type"], "application/json");
    assert.deepEqual(JSON.parse(received[0].body), { to: "agent-a", body: "hello" });
  });
});

test("a falsy body is sent as no body", async () => {
  // `if (body)` — so `null`, and also `0`/`""`, mean bodyless. Pinned as the CURRENT reading: a caller passing
  // `0` as a whole payload would be surprised, but no caller does, and DELETE/GET rely on the null case.
  await withServer((req, res) => json(res, 200, {}), async (baseUrl, received) => {
    const call = makeAifyHttpCall(baseUrl, "k");
    await call("DELETE", "/agents/one", null);
    assert.equal(received[0].body, "");
    assert.equal(received[0].headers["content-type"], undefined);
  });
});

// ── failures ────────────────────────────────────────────────────────────────

test("a non-OK response throws with the status ON the error, not just in its text", async () => {
  // Callers branch on `error.status` — the auto-registration path keys its whole tombstone decision on 410, and
  // a status only present inside a message string would have to be re-parsed to be used.
  await withServer((req, res) => {
    res.writeHead(410, { "Content-Type": "text/plain" });
    res.end("agent was intentionally removed");
  }, async (baseUrl) => {
    const call = makeAifyHttpCall(baseUrl, "k");
    await assert.rejects(() => call("POST", "/agents"), (error) => {
      assert.equal(error.status, 410, "the status did not reach the error object");
      assert.match(error.message, /HTTP 410/);
      assert.match(error.message, /intentionally removed/, "the server's explanation was dropped");
      return true;
    });
  });
});

test("an OK response with an unreadable body resolves to an empty object", async () => {
  // 204s and empty 200s are ordinary here. Throwing a parse error would turn a successful PATCH into a failed
  // one at every caller that does not need the answer.
  await withServer((req, res) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end("");
  }, async (baseUrl) => {
    assert.deepEqual(await makeAifyHttpCall(baseUrl, "k")("PATCH", "/agents/one/ready"), {});
  });
});

test("an OK response with NON-JSON text also resolves to an empty object", async () => {
  await withServer((req, res) => {
    res.writeHead(200, { "Content-Type": "text/plain" });
    res.end("OK");
  }, async (baseUrl) => {
    assert.deepEqual(await makeAifyHttpCall(baseUrl, "k")("GET", "/health"), {});
  });
});

test("a failure body that cannot be read still produces the status", async () => {
  // `.text().catch(() => "")` — a socket that dies mid-body must not replace the status with a read error.
  await withServer((req, res) => {
    res.writeHead(500, { "Content-Type": "text/plain", "Content-Length": "100" });
    res.end("short");
  }, async (baseUrl) => {
    await assert.rejects(() => makeAifyHttpCall(baseUrl, "k")("GET", "/agents"), (error) => {
      assert.equal(error.status, 500);
      return true;
    });
  });
});

// ── the deadline ────────────────────────────────────────────────────────────

test("a request to a service that never answers is ABORTED, not awaited forever", async () => {
  // The reason this module exists. A hung request stops the delivery loop, and an agent that has stopped
  // claiming looks exactly like an idle one — there is no error anywhere to notice.
  const started = process.hrtime.bigint();
  await withServer(() => { /* accept, never respond */ }, async (baseUrl) => {
    await assert.rejects(() => makeAifyHttpCall(baseUrl, "k")("GET", "/agents"), (error) => {
      // The abort surfaces as fetch's own error; what matters is that it surfaces at all, and quickly.
      assert.ok(error, "the hung request resolved instead of failing");
      return true;
    });
  });
  const elapsedMs = Number(process.hrtime.bigint() - started) / 1e6;
  assert.ok(elapsedMs < 8000,
    `the request took ${Math.round(elapsedMs)}ms — the AbortController deadline did not fire ` +
    "(AIFY_HTTP_TIMEOUT_MS was sealed to 1000 before import)");
});

test("an answered request does not leave its abort timer holding the process", async () => {
  // `clearTimeout` in the `finally`. Without it every answered request keeps a 20s handle alive, and a bridge
  // that finished its work waits out the last one before it can exit.
  const server = http.createServer((req, res) => json(res, 200, { ok: true }));
  await new Promise((resolve) => server.listen(0, "127.0.0.2", resolve));
  const baseUrl = `http://127.0.0.2:${server.address().port}`;
  try {
    const started = process.hrtime.bigint();
    await makeAifyHttpCall(baseUrl, "k")("GET", "/health");
    // A pending 1s timer would still be armed here; the assertion is that the module cleared it, which is
    // observable as the event loop having nothing left to wait for beyond our own await.
    const elapsedMs = Number(process.hrtime.bigint() - started) / 1e6;
    assert.ok(elapsedMs < 900, `the answered request took ${Math.round(elapsedMs)}ms`);
  } finally {
    server.closeAllConnections?.();
    await new Promise((resolve) => server.close(() => resolve()));
  }
});
