#!/usr/bin/env node
// A bridge that cannot read managed_via_wrapper claims no wrapper-eligible managed run (2026-10-14 audit, S1).
//
// The cache started as an empty set and the parser read an absent or malformed answer as empty, and an empty
// set means "nothing is wrapper-backed": a bridge whose first /settings read failed claimed and spawned managed
// codex and hermes runs itself, racing the wrapper child that owns them. Driven against a stand-in service.

import assert from "node:assert/strict";
import http from "node:http";

const answers = [];
const keyed = [];
const server = http.createServer((req, res) => {
  if (req.headers["x-api-key"] || req.headers.authorization) keyed.push(req.url);
  const next = answers.shift() || { status: 500, body: {} };
  res.writeHead(next.status, { "content-type": "application/json" });
  res.end(JSON.stringify(next.body));
});
await new Promise((resolve) => server.listen(0, "127.0.0.2", resolve));
// SEALED BEFORE THE BRIDGE MODULES LOAD (review of 0.9.5): every endpoint, fallback list and key a live
// wrapper exports is deleted, so the deliberate 500 below can reach only this fixture, with no key.
const { LIVE_ENV_CARRIERS } = await import("./_child-env.mjs");
for (const name of LIVE_ENV_CARRIERS) delete process.env[name];
process.env.AIFY_SERVER_URL = `http://127.0.0.2:${server.address().port}`;
process.env.CLAUDE_MCP_SERVER_URL = process.env.AIFY_SERVER_URL; // the legacy name is read first: both point here
process.env.AIFY_SERVICE_REGISTRY = `${process.env.TEMP || "/tmp"}/no-such-aify-registry-${process.pid}.json`;

const { WRAPPER_SET_UNKNOWN, managedViaWrapperRuntimesFromSettingsResponse: parse } = await import("../managed-wrapper-settings.js");
const { readManagedViaWrapperRuntimes } = await import("../managed-wrapper-cache.mjs");
const { supportedExecutionModes } = await import("../dispatch-execution.js");
const managedCodex = (set) => supportedExecutionModes({ sessionMode: "managed", runtime: "codex", capabilities: [] }, { managedViaWrapperRuntimes: set });

try {
  // The parser: only a real answer is a set.
  assert.equal(parse({}), WRAPPER_SET_UNKNOWN, "an absent setting is unknown, not empty");
  assert.equal(parse({ settings: { managed_via_wrapper: "yes" } }), WRAPPER_SET_UNKNOWN, "a malformed setting is unknown");
  assert.deepEqual([...parse({ settings: { managed_via_wrapper: [] } })], [], "CONTROL: a valid empty list is empty");
  assert.deepEqual([...parse({ managed_via_wrapper: false })], [], "CONTROL: an explicit false is empty");
  assert.deepEqual([...parse({ settings: { managed_via_wrapper: ["codex"] } })], ["codex"]);
  // A member that is not a string is malformed, not empty (review of 0.9.5: [null] read as "none").
  assert.equal(parse({ settings: { managed_via_wrapper: [null] } }), WRAPPER_SET_UNKNOWN, "[null] read as a valid empty set");
  assert.equal(parse({ settings: { managed_via_wrapper: [1, "codex"] } }), WRAPPER_SET_UNKNOWN, "a non-string member was dropped silently");
  assert.deepEqual([...parse({ settings: { managed_via_wrapper: ["codex", "a-newer-runtime"] } })], ["codex"],
    "CONTROL: an unrecognised runtime NAME is still filtered, for a newer service");

  // The cache, cold: a failed first read, then a malformed one, are both unknown.
  answers.push({ status: 500, body: {} });
  assert.equal(await readManagedViaWrapperRuntimes(), WRAPPER_SET_UNKNOWN, "a failed first read must not read as none");
  answers.push({ status: 200, body: { ok: true } });
  assert.equal(await readManagedViaWrapperRuntimes(), WRAPPER_SET_UNKNOWN, "a malformed answer must not read as none");
  // Recovery: the first valid answer is used.
  answers.push({ status: 200, body: { settings: { managed_via_wrapper: ["hermes"] } } });
  assert.deepEqual([...(await readManagedViaWrapperRuntimes())], ["hermes"]);
  // Warm: past the 5 s cache, a malformed [null] keeps the last valid set rather than replacing it.
  const realNow = Date.now;
  Date.now = () => realNow() + 10_000;
  try {
    answers.push({ status: 200, body: { settings: { managed_via_wrapper: [null] } } });
    assert.deepEqual([...(await readManagedViaWrapperRuntimes())], ["hermes"], "a warm [null] overwrote the last valid set");
  } finally {
    Date.now = realNow;
  }

  // The gate: unknown claims no managed codex run; a known answer decides as before.
  assert.deepEqual(managedCodex(WRAPPER_SET_UNKNOWN), [], "an unknown set claimed a wrapper-eligible managed run");
  assert.deepEqual(managedCodex(new Set()), ["managed"], "CONTROL: a known empty set still claims");
  assert.deepEqual(managedCodex(new Set(["codex"])), [], "CONTROL: a wrapper-backed runtime is left to the wrapper child");
  assert.deepEqual(supportedExecutionModes({ sessionMode: "managed", runtime: "pi", capabilities: [] }, { managedViaWrapperRuntimes: WRAPPER_SET_UNKNOWN }),
    ["managed"], "CONTROL: a runtime no wrapper backs is unaffected by unknown");
  assert.deepEqual(keyed, [], "a request carried a credential the seal should have removed");
} finally {
  server.close();
}
console.log("an-unknown-wrapper-set-claims-no-wrapper-run.test.js: all assertions passed");
