// The managed-via-wrapper runtime cache, tested by CALLING it against a real HTTP server.
//
// Extracted from server.js in v0.5.4, where nothing could reach it. The dispatch loop asks this on every
// claim to learn which runtimes to SKIP — those belong to a wrapper's own child bridge. Two properties
// carry the risk and neither is visible from the call site:
//
//   * the 5-second cache is the only thing stopping a claim loop hammering /settings, and
//   * the catch returns the STALE set, not an empty one. An empty set means "skip nothing", so on any
//     blip of /settings this bridge would start claiming work belonging to a wrapper's child — which is
//     the double-claim class this repo has been bitten by before.
//
// The fake service binds 127.0.0.2 and `AIFY_SERVER_URL` is set BEFORE the import, because
// aify-service-endpoint.mjs reads it at module load, once per process. That is the established shape for
// bridge modules whose HTTP boundary cannot be monkey-patched — ESM bindings are read-only.

import assert from "node:assert/strict";
import test from "node:test";
import http from "node:http";

let HANDLER = (_req, res) => { res.writeHead(200); res.end("{}"); };
let CALLS = 0;
const SERVER = http.createServer((req, res) => { CALLS += 1; HANDLER(req, res); });
const PORT = await new Promise((resolve) => {
  SERVER.listen(0, "127.0.0.2", () => resolve(SERVER.address().port));
});

process.env.AIFY_SERVER_URL = `http://127.0.0.2:${PORT}`;
process.env.AIFY_API_KEY = "test-key";
const m = await import("../managed-wrapper-cache.mjs");

test.after(() => SERVER.close());

const serve = (body) => { HANDLER = (_req, res) => { res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify(body)); }; };
const fail = () => { HANDLER = (_req, res) => { res.writeHead(500); res.end("boom"); }; };

/**
 * Wait the 5-second window out, then serve `body`.
 *
 * THE CACHE IS MODULE STATE SHARED BY EVERY TEST IN THIS FILE, and its TTL is compared against a real
 * `Date.now()` — the body is byte-identical to what left server.js, so there is no clock to inject.
 * Any test needing a genuine fetch must therefore expire the window first; two of these asserted
 * nothing until they did, because they were quietly served the previous test's value.
 */
async function freshlyServing(bodyToServe) {
  await new Promise((r) => setTimeout(r, 5100));
  serve(bodyToServe);
}

test("it reads the runtimes out of /settings, and a second read inside the window does NOT", async () => {
  // Both halves in one test because they share a warm cache by construction: the cached read is only
  // meaningful immediately after the fetch that filled it. The cache is the only thing standing between
  // a per-claim dispatch loop and one /settings request per claim attempt, per bridge, forever.
  serve({ settings: { managed_via_wrapper: ["hermes", "codex"] } });
  CALLS = 0;
  const set = await m.readManagedViaWrapperRuntimes();
  assert.ok(set instanceof Set);
  assert.ok(CALLS >= 1, "the first read must actually hit the service");
  assert.deepEqual([...set].sort(), ["codex", "hermes"]);

  CALLS = 0;
  assert.deepEqual([...(await m.readManagedViaWrapperRuntimes())].sort(), ["codex", "hermes"]);
  assert.equal(CALLS, 0, "a cached read must make no request");
});

test("A FAILED READ RETURNS THE STALE SET, NOT AN EMPTY ONE", async () => {
  // The property that matters most. An empty set means "skip nothing", so an empty-on-error fallback
  // would have this bridge claim a wrapper child's work during any /settings blip — the double-claim
  // class this repo has been bitten by. Warmed with a NON-empty value so staleness is distinguishable
  // from the empty result a failure would otherwise produce.
  await freshlyServing({ settings: { managed_via_wrapper: ["hermes", "codex"] } });
  const warmed = [...(await m.readManagedViaWrapperRuntimes())].sort();
  assert.deepEqual(warmed, ["codex", "hermes"]);

  await new Promise((r) => setTimeout(r, 5100));
  fail();
  assert.deepEqual([...(await m.readManagedViaWrapperRuntimes())].sort(), warmed,
    "the stale set must survive a failed refresh");
});

test("`managed_via_wrapper: true` means EVERY wrapper-backed runtime", async () => {
  await freshlyServing({ settings: { managed_via_wrapper: true } });
  assert.deepEqual([...(await m.readManagedViaWrapperRuntimes())].sort(), ["codex", "hermes"]);
});

test("a comma-separated STRING is NOT the contract, and fails silently — as does an absent setting", async () => {
  // I wrote this suite against a string first. The parser takes an ARRAY or the literal `true`; a string
  // yields an empty set, which is the "skip nothing" value — so the mistake reads as a working cache
  // serving a dangerous default, with nothing erroring. Read the callee's schema before writing the call.
  await freshlyServing({ settings: { managed_via_wrapper: "hermes,codex" } });
  assert.deepEqual([...(await m.readManagedViaWrapperRuntimes())], []);

  await freshlyServing({ settings: {} });
  assert.deepEqual([...(await m.readManagedViaWrapperRuntimes())], [], "an absent setting is also empty");
});
