#!/usr/bin/env node
// Which reply policy is in force when a run ends without one — and what happens when nobody answers.
//
// `readReplyCaptureFallback` decides between two behaviours at the moment a required-reply run ends:
//
//   fallback ON (default) — mirror the agent's final output as the reply, so a managed turn that
//                           answered in its console still closes the run
//   strict                — never fabricate a reply; record that one is still owed
//
// It was named by no test. Three things about it are load-bearing and none of them is obvious from
// the call site:
//
//   * IT DEFAULTS TO TRUE when the setting is absent or null, because an unset setting must not
//     silently switch the fleet into strict mode and leave runs unanswered;
//   * IT SERVES STALE ON FAILURE rather than falling back to a default. This runs at TURN END, when
//     the service may be exactly what is unreachable — and a failed settings fetch deciding the
//     reply policy by accident is how a run gets a fabricated answer, or none, for no reason
//     anybody chose;
//   * IT CACHES FOR 5 SECONDS, because a busy fleet ends turns constantly and each one would
//     otherwise re-fetch /settings.
//
// Driven against a FAKE service in a child process bound to 127.0.0.2, and the child's env is
// sealed: `defaultFallbackServerUrls` only adds the real service when the primary URL is 127.0.0.1
// or localhost, so there is no path from here to the live fleet.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const MODULE = pathToFileURL(path.join(HERE, "..", "required-reply-handoff.mjs")).href;

/**
 * Ask the real module for the policy, against a fake /settings that returns `bodies[i]` for the i-th
 * request (the last one repeating), or 500s when `fail` is set. Returns { answers, requests }.
 */
function readPolicy({ bodies = ['{}'], calls = 1, failAfter = null, sleepMs = 0 }) {
  const script = [
    'import http from "node:http";',
    "let seen = 0;",
    `const bodies = ${JSON.stringify(bodies)};`,
    "const srv = http.createServer((req, res) => {",
    "  seen += 1;",
    `  const fail = ${failAfter === null ? "false" : `seen > ${failAfter}`};`,
    "  if (fail) {",
    '    res.writeHead(500, { "content-type": "application/json" });',
    '    res.end("{}");',
    "    return;",
    "  }",
    '  res.writeHead(200, { "content-type": "application/json" });',
    "  res.end(bodies[Math.min(seen - 1, bodies.length - 1)]);",
    "});",
    'await new Promise((r) => srv.listen(0, "127.0.0.2", r));',
    'process.env.AIFY_SERVER_URL = "http://127.0.0.2:" + srv.address().port;',
    'process.env.CLAUDE_MCP_SERVER_URL = "";',
    `const m = await import(${JSON.stringify(MODULE)});`,
    "const answers = [];",
    `for (let i = 0; i < ${calls}; i += 1) {`,
    `  if (i > 0 && ${sleepMs} > 0) await new Promise((r) => setTimeout(r, ${sleepMs}));`,
    "  answers.push(await m.readReplyCaptureFallback());",
    "}",
    "srv.close();",
    "process.stdout.write(JSON.stringify({ answers, requests: seen }));",
  ].join("\n");
  return JSON.parse(execFileSync(process.execPath, ["--input-type=module", "-e", script], {
    env: { ...process.env, AIFY_SERVER_URL: "", CLAUDE_MCP_SERVER_URL: "", AIFY_API_KEY: "" },
    encoding: "utf-8",
    stdio: ["ignore", "pipe", "pipe"],
  }));
}

test("an explicit false selects strict mode", () => {
  const got = readPolicy({ bodies: ['{"settings":{"managed_reply_capture_fallback":false}}'] });
  assert.deepEqual(got.answers, [false]);
});

test("an explicit true selects the mirroring safety net", () => {
  const got = readPolicy({ bodies: ['{"settings":{"managed_reply_capture_fallback":true}}'] });
  assert.deepEqual(got.answers, [true]);
});

test("an ABSENT setting means the safety net, not strict mode", () => {
  // The direction matters. Defaulting to strict on a missing key would leave every required-reply
  // run unanswered across the fleet, and the setting is absent on any service that never had it.
  for (const body of ["{}", '{"settings":{}}', '{"settings":{"managed_reply_capture_fallback":null}}']) {
    const got = readPolicy({ bodies: [body] });
    assert.deepEqual(got.answers, [true], `body ${body} did not default to the safety net`);
  }
});

test("a settings response with no envelope is still read", () => {
  // The service returns {settings:{...}}; a bare object is accepted too, and the reader has to cope
  // with both or the policy silently reverts to the default on one of them.
  const got = readPolicy({ bodies: ['{"managed_reply_capture_fallback":false}'] });
  assert.deepEqual(got.answers, [false]);
});

test("a truthy non-boolean is coerced rather than treated as absent", () => {
  const got = readPolicy({ bodies: ['{"settings":{"managed_reply_capture_fallback":0}}'] });
  assert.deepEqual(got.answers, [false], "0 must mean strict, not 'unset'");
});

test("the answer is CACHED — a burst of turn-ends fetches /settings once", () => {
  const got = readPolicy({
    bodies: ['{"settings":{"managed_reply_capture_fallback":false}}'], calls: 5,
  });
  assert.deepEqual(got.answers, [false, false, false, false, false]);
  assert.equal(got.requests, 1, "each turn end re-fetched the settings");
});

test("a FAILED fetch serves the cached answer instead of reverting to the default", () => {
  // The important one. This runs at turn end, when the service may be exactly what is unreachable.
  // Reverting to `true` here would auto-mirror a reply on a fleet deliberately running strict —
  // fabricating an answer because a settings fetch failed.
  //
  // THE 5-SECOND SLEEP IS THE TEST. Inside the cache window the reader never reaches its failure
  // path at all — my first version asserted this property against two calls a millisecond apart and
  // the second answer came from the cache, so a mutation replacing the stale-cache return with a
  // hardcoded default survived. The TTL is not injectable, so the wait is real.
  const got = readPolicy({
    bodies: ['{"settings":{"managed_reply_capture_fallback":false}}'],
    calls: 2, failAfter: 1, sleepMs: 5100,
  });
  assert.deepEqual(got.answers, [false, false],
    "a failed settings fetch changed the reply policy");
});

test("a failure with NOTHING cached yet answers with the safety net", () => {
  // No evidence at all, and the two directions are not symmetric: mirroring a reply that was
  // already sent is a duplicate, while withholding one strands the run and the sender.
  const got = readPolicy({ bodies: ["{}"], calls: 1, failAfter: 0 });
  assert.deepEqual(got.answers, [true]);
});
