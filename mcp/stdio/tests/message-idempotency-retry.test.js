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
// v0.5.4b: `comms_send` moved to `send-tools.mjs`, so the nonce assertions read THAT file now.
// AND THE UPGRADE THIS FILE ASKS FOR IS NOW UNBLOCKED: the note above says these are source-text
// assertions only because server.js runs-on-import, and that they could become real behavioural tests
// once the tool lives in a leaf that imports cleanly. It does — `tests/send-tools.test.js` imports
// send-tools.mjs and CALLS both handlers. Turning these into a real POST-body assertion needs a
// loopback service and IS_REMOTE true at load, which is a separate slice, so the precondition is
// recorded here rather than the work being half-done.
const server = fs.readFileSync(path.join(__dirname, "..", "send-tools.mjs"), "utf8");

// The retry predicate's nonce gate (sections 1-3 of this file until 2026-09-18) is proven by calling it:
// `aify-service-endpoint.test.js` for the predicate, including a blank nonce, and
// `http-retry-policy.test.js` for the real retry loop against a flaky service. What is left here is the
// sender's half, which nothing runs yet.

// Both send paths mint a clientNonce.
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
