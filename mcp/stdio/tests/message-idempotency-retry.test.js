#!/usr/bin/env node
// Task #240: /messages/send retry is gated on a client nonce so a transient socket
// error can be retried safely (server collapses the retry) WITHOUT double-sending a
// nonce-less body. server.js runs-on-import (MCP entrypoint), so — like the other
// server-*.test.js suites — these are source-text assertions that lock the wiring.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const server = fs.readFileSync(path.join(__dirname, "..", "server.js"), "utf8");

// 1. The retry predicate takes the body and gates /messages/send on a NON-EMPTY nonce.
assert.match(
  server,
  /function isRetriableRequest\(method, endpoint, body = null\)/,
  "isRetriableRequest must accept the request body to inspect the nonce",
);
assert.match(
  server,
  /path === "\/messages\/send" && body && typeof body === "object" && String\(body\.clientNonce \|\| ""\)\.trim\(\)/,
  "/messages/send must be retriable ONLY when the body carries a non-empty clientNonce",
);

// 2. /messages/send must NOT be in the unconditional retriable set (that would retry
//    nonce-less sends and double-send).
const setMatch = server.match(/const RETRIABLE_POST_PATHS = new Set\(\[([\s\S]*?)\]\)/);
assert.ok(setMatch, "RETRIABLE_POST_PATHS set must exist");
assert.doesNotMatch(
  setMatch[1],
  /\/messages\/send/,
  "/messages/send must be conditionally retriable (nonce-gated), never unconditional",
);

// 3. httpCall passes the body into the retriability check.
assert.match(
  server,
  /const retriable = isRetriableRequest\(method, endpoint, body\)/,
  "httpCall must pass the body to isRetriableRequest",
);

// 4. Both send paths mint a clientNonce.
//    a) comms_send: a fresh per-call random nonce.
assert.match(
  server,
  /const clientNonce = randomUUID\(\);[\s\S]*?"\/messages\/send"[\s\S]*?clientNonce,/,
  "comms_send must mint a per-call clientNonce and include it in the send body",
);
//    b) the owed-reply handoff: a deterministic run-keyed nonce (dedups + retry-safe).
assert.match(
  server,
  /clientNonce: `handoff-\$\{run\.id\}-\$\{terminalStatus\}`/,
  "the auto-reply handoff must use a deterministic run-keyed nonce",
);

console.log("message-idempotency-retry.test.js: all assertions passed");
