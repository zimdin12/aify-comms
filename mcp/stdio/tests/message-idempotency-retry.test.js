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
// v0.5.4: `isRetriableRequest`, `RETRIABLE_POST_PATHS` and `httpCall` moved out of server.js into
// `aify-service-endpoint.mjs` (layer 0 of the server.js decomposition). Repointed rather than deleted:
// the retry/nonce policy is unchanged, only its address is.
//
// WORTH UPGRADING LATER: this file reads SOURCE because server.js runs-on-import as the MCP entry
// point, which made importing it impossible. That reason no longer applies to these three — they now
// live in a leaf that imports cleanly, so these assertions could become real behavioural tests that
// call the functions (see tests/aify-service-endpoint.test.js, which already does for the predicates).
// Left as source-pinning here to keep this slice a relocation rather than a test rewrite.
const server = fs.readFileSync(path.join(__dirname, "..", "server.js"), "utf8");
const endpoint = fs.readFileSync(path.join(__dirname, "..", "aify-service-endpoint.mjs"), "utf8");

// 1. The retry predicate takes the body and gates /messages/send on a NON-EMPTY nonce.
assert.match(
  endpoint,
  /function isRetriableRequest\(method, endpoint, body = null\)/,
  "isRetriableRequest must accept the request body to inspect the nonce",
);
assert.match(
  endpoint,
  /path === "\/messages\/send" && body && typeof body === "object" && String\(body\.clientNonce \|\| ""\)\.trim\(\)/,
  "/messages/send must be retriable ONLY when the body carries a non-empty clientNonce",
);

// 2. /messages/send must NOT be in the unconditional retriable set (that would retry
//    nonce-less sends and double-send).
const setMatch = endpoint.match(/const RETRIABLE_POST_PATHS = new Set\(\[([\s\S]*?)\]\)/);
assert.ok(setMatch, "RETRIABLE_POST_PATHS set must exist");
assert.doesNotMatch(
  setMatch[1],
  /\/messages\/send/,
  "/messages/send must be conditionally retriable (nonce-gated), never unconditional",
);

// 3. httpCall passes the body into the retriability check.
assert.match(
  endpoint,
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
//    b) the owed-reply handoff's nonce was asserted here as source text until v0.5.4, when
//       `ensureRequiredReplyHandoff` moved to `required-reply-handoff.mjs`. It is now asserted BEHAVIOURALLY
//       in `required-reply-handoff.test.js`: the POST body's clientNonce is read back from a real request,
//       and a second test proves the same run+status mints the SAME nonce (so a retry dedups) while a
//       different status mints a different one. That is strictly more than a regex could show — this one
//       matched the template literal and would have passed on a nonce built from the wrong fields.

console.log("message-idempotency-retry.test.js: all assertions passed");
