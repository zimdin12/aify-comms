#!/usr/bin/env node
// How many times the bridge retries a failed call, and how long it waits between tries.
//
// HTTP_RETRY_ATTEMPTS and HTTP_RETRY_BASE_MS were named by no test. They decide whether a dispatch
// survives a service restart or is lost to a single 503 — and, in the other direction, whether a
// non-idempotent POST is delivered twice.
//
// DRIVEN AGAINST A FAKE SERVICE, in a child process, bound to 127.0.0.2. The same reasoning as
// `agent-heartbeat.test.js`: `defaultFallbackServerUrls` only adds the real service when the primary
// URL is exactly 127.0.0.1 or localhost, so binding 127.0.0.2 means there is no path to the live
// service on this host at all. A retry test that reached production would post the same body three
// times to a running fleet.
//
// THE INTERESTING HALF IS WHAT IS *NOT* RETRIED. 5xx is retried only on a call that is safe to
// repeat, and `/messages/send` is safe only when the body carries a clientNonce the server collapses
// on. A nonce-less send retried three times is three messages in someone's inbox — so the count is
// asserted at 1 for that case, not merely "fewer than three".

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { HTTP_RETRY_ATTEMPTS, HTTP_RETRY_BASE_MS } from "../aify-service-endpoint.mjs";
import { sealedChildEnv } from "./_child-env.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
// A file:// URL, not a Windows path: `import("C:/...")` is rejected by the ESM loader
// as an unsupported "c:" scheme.
const ENDPOINT = pathToFileURL(path.join(HERE, "..", "aify-service-endpoint.mjs")).href;

/**
 * Run one httpCall against a fake service that answers `status` for the first `failures` requests
 * and 200 afterwards. Returns {requests, ms, failed} from the child.
 */
function callAgainstFlakyService({ method, endpoint, body = null, failures, status = 503 }) {
  const script = [
    'import http from "node:http";',
    "let seen = 0;",
    "const srv = http.createServer((req, res) => {",
    "  seen += 1;",
    '  let raw = "";',
    '  req.on("data", (c) => { raw += c; });',
    '  req.on("end", () => {',
    `    if (seen <= ${failures}) {`,
    `      res.writeHead(${status}, { "content-type": "application/json" });`,
    '      res.end("{}");',
    "      return;",
    "    }",
    '    res.writeHead(200, { "content-type": "application/json" });',
    '    res.end("{}");',
    "  });",
    "});",
    'await new Promise((r) => srv.listen(0, "127.0.0.2", r));',
    'process.env.AIFY_SERVER_URL = "http://127.0.0.2:" + srv.address().port;',
    'process.env.CLAUDE_MCP_SERVER_URL = "";',
    `const m = await import(${JSON.stringify(ENDPOINT)});`,
    "const started = Date.now();",
    "let failed = false;",
    "try {",
    `  await m.httpCall(${JSON.stringify(method)}, ${JSON.stringify(endpoint)}, ${JSON.stringify(body)});`,
    "} catch { failed = true; }",
    "const ms = Date.now() - started;",
    "srv.close();",
    "process.stdout.write(JSON.stringify({ requests: seen, ms, failed }));",
  ].join("\n");
  return JSON.parse(execFileSync(process.execPath, ["--input-type=module", "-e", script], {
    // The env is SEALED: the child must not inherit a server URL from the operator's shell, or the
    // fake service is bypassed and the calls land somewhere real.
    env: { ...sealedChildEnv(), AIFY_SERVER_URL: "", CLAUDE_MCP_SERVER_URL: "", AIFY_API_KEY: "" },
    encoding: "utf-8",
    stdio: ["ignore", "pipe", "pipe"],
  }));
}

test("the constants are a sane retry policy on their face", () => {
  assert.ok(Number.isInteger(HTTP_RETRY_ATTEMPTS) && HTTP_RETRY_ATTEMPTS >= 2,
    "a single attempt is not a retry policy");
  assert.ok(HTTP_RETRY_ATTEMPTS <= 5,
    "attempts multiply every request the bridge makes; this is a bridge, not a queue");
  assert.ok(Number.isInteger(HTTP_RETRY_BASE_MS) && HTTP_RETRY_BASE_MS > 0);
});

test("the SHIPPED policy is 3 attempts, 250ms base — pinned as a number", () => {
  // THE BEHAVIOUR TESTS BELOW DERIVE THEIR EXPECTATIONS FROM THESE CONSTANTS, which is what makes
  // them survive a deliberate change — and also what let a mutation raising the count from 3 to 4
  // pass every one of them: both sides of the assertion moved together. Verified by mutation, so
  // the number itself is pinned here as well. It is a contract with the service, not a local
  // preference: each attempt multiplies the load a struggling service sees, and the total backoff
  // has to stay inside the caller's window. Changing it is a decision — update this line on
  // purpose.
  assert.equal(HTTP_RETRY_ATTEMPTS, 3);
  assert.equal(HTTP_RETRY_BASE_MS, 250);
});

test("a retriable call survives transient 5xx up to the attempt limit", () => {
  // Two failures then success: the call must succeed, having tried three times.
  const got = callAgainstFlakyService({
    method: "POST", endpoint: "/agents/lc-a/heartbeat", body: { turnBusy: true },
    failures: HTTP_RETRY_ATTEMPTS - 1,
  });
  assert.equal(got.failed, false, "a call that would have succeeded on the last attempt failed");
  assert.equal(got.requests, HTTP_RETRY_ATTEMPTS);
});

test("it gives up after exactly HTTP_RETRY_ATTEMPTS, not more", () => {
  const got = callAgainstFlakyService({
    method: "POST", endpoint: "/agents/lc-a/heartbeat", body: { turnBusy: true },
    failures: 99,
  });
  assert.equal(got.failed, true);
  assert.equal(got.requests, HTTP_RETRY_ATTEMPTS,
    "an off-by-one here is either a lost call or an extra one against a struggling service");
});

test("the wait between attempts GROWS — it is a backoff, not a tight loop", () => {
  const got = callAgainstFlakyService({
    method: "POST", endpoint: "/agents/lc-a/heartbeat", body: { turnBusy: true },
    failures: 99,
  });
  // 250 + 500 for the default policy. Asserted from the constants rather than the numbers, so the
  // test follows a deliberate change and still fails a linear or zero backoff.
  let expected = 0;
  for (let attempt = 1; attempt < HTTP_RETRY_ATTEMPTS; attempt += 1) {
    expected += HTTP_RETRY_BASE_MS * 2 ** (attempt - 1);
  }
  assert.ok(got.ms >= expected * 0.8,
    `retries took ${got.ms}ms, expected at least ~${expected}ms of backoff — a service that is `
      + "down is not helped by three requests in the same millisecond");
});

test("a 4xx is NOT retried, however retriable the call is", () => {
  // A real error. Repeating it wastes the window the caller has, and for a 409 it can turn one
  // refusal into three log lines that read like three separate incidents.
  const got = callAgainstFlakyService({
    method: "POST", endpoint: "/agents/lc-a/heartbeat", body: { turnBusy: true },
    failures: 99, status: 409,
  });
  assert.equal(got.failed, true);
  assert.equal(got.requests, 1);
});

test("a NONCE-LESS /messages/send is never retried", () => {
  // THE ONE THAT MATTERS. The server collapses a retry only when the body carries a clientNonce;
  // without one, three attempts are three messages in the recipient's inbox.
  const got = callAgainstFlakyService({
    method: "POST", endpoint: "/messages/send", body: { to: "lc-b", body: "hello" }, failures: 99,
  });
  assert.equal(got.failed, true);
  assert.equal(got.requests, 1, "a nonce-less send was retried and would have double-delivered");
});

test("a send WITH a clientNonce is retried, because the server collapses it", () => {
  const got = callAgainstFlakyService({
    method: "POST", endpoint: "/messages/send",
    body: { to: "lc-b", body: "hello", clientNonce: "n-1" }, failures: HTTP_RETRY_ATTEMPTS - 1,
  });
  assert.equal(got.failed, false);
  assert.equal(got.requests, HTTP_RETRY_ATTEMPTS);
});
