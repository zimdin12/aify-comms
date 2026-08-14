// Real tests for the boot tombstoned-marker sweep, extracted from server.js in v0.5.4.
//
// THE FAIL-SAFE IS THE WHOLE FUNCTION and it is asymmetric. `sweepTombstonedMarkers` deletes markers for
// every agent NOT in the keyset it is handed, so a keyset that is too SMALL destroys markers belonging to
// live agents, while one that is too large merely does nothing. A failed `/agents` query must therefore
// sweep nothing — except a 404, which genuinely means "no agents yet" and makes the empty keyset correct
// rather than missing. Those two failures look identical in a log and mean opposite things.
//
// server.js is imported by no test, so none of this had coverage.
//
// WHAT THESE TESTS DO **NOT** COVER, said plainly so three green ticks are not mistaken for more.
// The fail-safe branches above are NOT exercised. `IS_ENVIRONMENT_BRIDGE` is read from the environment at
// module load, and the guard returns before the `/agents` query in any process where it is false — which
// is every test process. Making it true would mean setting AIFY_ENVIRONMENT_BRIDGE, and that is a
// forbidden move in this repo: a test run that set it BECAME the environment bridge, superseded the live
// one and reaped seven gateway hosts (2026-08-13). A role flag is not a config knob.
//
// Reaching that logic needs the flag and the keyset passed IN rather than read from module scope — a
// small redesign, not a byte-identical relocation, so it is out of scope for this slice. What is covered
// here is the guard itself, the module's independence from the offline reaper, and that importing it has
// no load-time side effects.
//
// A REAL HTTP SERVER on 127.0.0.2: `httpCall` is an imported binding and cannot be monkey-patched. One
// server for the file with a swappable handler — a per-test server plus a cache-busted import does not
// bust `aify-service-endpoint.mjs`, which resolves its target once at load.

import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

let HANDLER = (_res) => {};
const REQUESTS = [];
const SERVER = http.createServer((req, res) => {
  req.on("data", () => {});
  req.on("end", () => {
    REQUESTS.push(req.url.replace(/^\/api\/v1/, ""));
    HANDLER(res);
  });
});

const PORT = await new Promise((r) => SERVER.listen(0, "127.0.0.2", () => r(SERVER.address().port)));
process.env.AIFY_SERVER_URL = `http://127.0.0.2:${PORT}`;
process.env.AIFY_API_KEY = "test-key";

// IS_ENVIRONMENT_BRIDGE is read at load from the environment, and the guard short-circuits without it —
// so it is set BEFORE the import, and the first test proves the guard rather than assuming it.
process.env.AIFY_ENVIRONMENT_BRIDGE_TEST_ONLY = "";
const m = await import("../boot-marker-sweep.mjs");
const { IS_ENVIRONMENT_BRIDGE } = await import("../launch-identity.mjs");

test.after(() => SERVER.close());

function scenario(handler) {
  HANDLER = handler;
  REQUESTS.length = 0;
  return REQUESTS;
}

test("a non-environment-bridge process sweeps nothing and asks nothing", async () => {
  // This test process is not a bridge, which is what makes the guard observable here: if it ever stopped
  // short-circuiting, this suite would start making real /agents queries.
  assert.equal(IS_ENVIRONMENT_BRIDGE, false, "the fixture assumes a non-bridge process");
  const requests = scenario((res) => { res.writeHead(200); res.end("{}"); });
  await m.runBootTombstonedMarkerSweep();
  assert.deepEqual(requests, [], "the guard must return before the service is contacted");
});

test("the sweep is exported from its own module rather than the reaper", async () => {
  // `sweepTombstonedMarkers` lives in reap-managed-survivors.js, which has NO service dependency — zero
  // httpCall, zero fetch. This orchestrator's first act is a service query, so it deliberately does not
  // live there. Asserted so a later tidy-up does not merge them and hand that module a network dependency.
  assert.equal(typeof m.runBootTombstonedMarkerSweep, "function");
  const reaper = await import("../reap-managed-survivors.js");
  assert.equal(typeof reaper.sweepTombstonedMarkers, "function", "the primitive stays where it was");
  assert.equal(reaper.runBootTombstonedMarkerSweep, undefined,
    "the orchestrator must NOT have been merged into the offline reaper module");
});

test("the module imports cleanly outside a browser and without a live service", async () => {
  // It is loaded at bridge boot before anything is reachable; a module-scope call to the service here
  // would make importing it fail on a cold start.
  const again = await import("../boot-marker-sweep.mjs");
  assert.equal(again.runBootTombstonedMarkerSweep, m.runBootTombstonedMarkerSweep,
    "one module instance, no load-time side effects");
});
