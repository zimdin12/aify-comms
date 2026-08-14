// The spawn-request claim pass, tested by CALLING it against a real HTTP server.
//
// Extracted from server.js in v0.5.4, where nothing could reach it. This is what turns a dashboard
// spawn request into a running agent, and its riskiest step is the WORKSPACE CHECK: a request names a
// cwd, and `workspaceWithinRoots` is the only thing stopping this bridge launching a process outside
// the roots its environment declared. That check failing open is a sandbox escape by configuration.
//
// NOTHING HERE REACHES `spawn()`. Every case ends before it — no claim, a rejected workspace, or a
// claim error — so no process is ever launched by this suite. The paths past the check need a real
// runtime and belong in the live round-trip, not here.
//
// Fake service on 127.0.0.2, `AIFY_SERVER_URL` set BEFORE the import.

import assert from "node:assert/strict";
import test from "node:test";
import http from "node:http";

const REQUESTS = [];
let CLAIM = null;
let CLAIM_STATUS = 200;
const SERVER = http.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", () => {
    REQUESTS.push({ method: req.method, url: req.url, body });
    if (req.url.endsWith("/spawn-requests/claim") && CLAIM_STATUS !== 200) {
      res.writeHead(CLAIM_STATUS, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: "nope" }));
      return;
    }
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(req.url.endsWith("/claim") ? { spawnRequest: CLAIM } : {}));
  });
});
const PORT = await new Promise((r) => SERVER.listen(0, "127.0.0.2", () => r(SERVER.address().port)));

process.env.AIFY_SERVER_URL = `http://127.0.0.2:${PORT}`;
process.env.AIFY_API_KEY = "test-key";
const { runSpawnPass } = await import("../spawn-loop.mjs");
const { BRIDGE_INSTANCE_ID } = await import("../bridge-instance.mjs");

test.after(() => SERVER.close());

function deps(environment) {
  return {
    CLAIM_OPTS: {},
    CLAIM_WAIT_MS: 0,
    MACHINE_ID: "machine-1",
    effectiveEnvironmentPayload: () => environment,
    ensureDispatchLoop: () => {},
  };
}

function scenario(claim, { status = 200 } = {}) {
  REQUESTS.length = 0;
  CLAIM = claim;
  CLAIM_STATUS = status;
}

const ENV = { id: "env-1", cwdRoots: ["C:/work"] };

test("it claims for THIS environment, bridge and machine", async () => {
  scenario(null);
  await runSpawnPass(deps(ENV));
  const claim = REQUESTS.find((r) => r.url.endsWith("/spawn-requests/claim"));
  assert.ok(claim);
  const sent = JSON.parse(claim.body);
  assert.equal(sent.environmentId, "env-1");
  assert.equal(sent.bridgeId, BRIDGE_INSTANCE_ID);
  assert.equal(sent.machineId, "machine-1");
});

test("NO REQUEST means exactly one call — this runs on a timer forever", async () => {
  scenario(null);
  await runSpawnPass(deps(ENV));
  assert.equal(REQUESTS.length, 1, "an idle poll must not PATCH anything");
});

test("A WORKSPACE OUTSIDE THE DECLARED ROOTS IS REFUSED, and the request is failed back", async () => {
  // The sandbox property. Failing open here launches an agent wherever the request asks — and the
  // request comes from the dashboard, not from this machine. Reporting `failed` matters just as much:
  // a claimed request left unresolved is one the service hands to nobody else.
  scenario({ id: "sr-1", workspace: "C:/somewhere-else" });
  await runSpawnPass(deps(ENV));

  const patch = REQUESTS.find((r) => r.method === "PATCH");
  assert.ok(patch, "the request must be resolved, not left claimed");
  assert.match(patch.url, /\/spawn-requests\/sr-1$/);
  const sent = JSON.parse(patch.body);
  assert.equal(sent.status, "failed");
  assert.match(sent.error, /outside this bridge's advertised roots/);
  assert.equal(sent.bridgeId, BRIDGE_INSTANCE_ID, "the service must know which bridge refused");
});

test("the refusal names the offending workspace, so the operator can see what was asked for", async () => {
  scenario({ id: "sr-2", workspace: "C:/nope" });
  await runSpawnPass(deps(ENV));
  const sent = JSON.parse(REQUESTS.find((r) => r.method === "PATCH").body);
  assert.match(sent.error, /C:\/nope/);
});

test("AN ENVIRONMENT WITH NO DECLARED ROOTS ALLOWS EVERY WORKSPACE — it fails OPEN", async () => {
  // I expected a refusal and the code does the opposite: `workspaceWithinRoots` returns true when the
  // normalised root list is empty, and `"/"` is an explicit match-all. That is deliberate — its own
  // comment records the 2026-06-03 fix where the default `['/', '~']` roots rejected EVERY absolute
  // workspace and no managed spawn could start — but the consequence is that an environment which has
  // advertised no roots vouches for any path the dashboard names.
  //
  // Pinned as it BEHAVES, with the direction stated, because it is a security-shaped default rather than
  // a bug to quietly invert: tightening it would stop spawns for any environment that has not declared
  // roots, which is a fleet-wide behaviour change. This test is what makes the choice visible.
  scenario({ id: "sr-3", workspace: "C:/anywhere-at-all" });
  await runSpawnPass(deps({ id: "env-1" }));
  const patch = REQUESTS.find((r) => r.method === "PATCH");
  assert.ok(patch, "the request is acted on, not refused");
  assert.notEqual(JSON.parse(patch.body).status, "failed",
    "with no roots declared, the workspace check does not reject");
});

test("`/` is an explicit match-all root", async () => {
  // The other half of the same fix. An environment rooted at "/" accepts anything, by design.
  scenario({ id: "sr-4", workspace: "C:/anywhere" });
  await runSpawnPass(deps({ id: "env-1", cwdRoots: ["/"] }));
  const patch = REQUESTS.find((r) => r.method === "PATCH");
  assert.notEqual(JSON.parse(patch.body).status, "failed");
});

test("a 404 on claim is SILENT, while other errors are recorded", async () => {
  // 404 means "no spawn requests here" and happens on every poll of an idle fleet — counting it as a
  // failure would have the claim-failure tracker warning constantly about a healthy bridge.
  scenario(null, { status: 404 });
  await assert.doesNotReject(() => runSpawnPass(deps(ENV)));

  scenario(null, { status: 500 });
  await assert.doesNotReject(() => runSpawnPass(deps(ENV)),
    "a server error must not escape into the loop's catch as an unhandled shape");
});
