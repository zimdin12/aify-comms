// Real tests for the managed-ownership read, extracted from server.js in v0.5.4.
//
// THIS IS THE READ THE REAPING DECISION IS MADE FROM. It returns "these managed agents belong to THIS
// environment, and here is whether the bridge that owns each one is still alive" — and a teardown pass
// consumes it. An agent that wrongly passes the membership filter belongs to a DIFFERENT environment, and
// reaping it kills someone else's worker; an agent wrongly reported `ownerLive` survives a sweep meant to
// collect it. Neither direction had a single test, because server.js is imported by no test.
//
// A REAL HTTP SERVER on 127.0.0.2. `httpCall` is an imported binding and cannot be monkey-patched, and
// `aify-service-endpoint.mjs` resolves its target ONCE at load — so the endpoint env vars are set before
// the import, and one server serves the whole file with a swappable route table.
//
// `bridgeOwnerIsLive` and `workspaceWithinRoots` are NOT stubbed. They are the real collaborators, and
// stubbing them would leave the two things most worth checking — membership and liveness — asserted
// against my idea of what they do rather than what they do.

import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

let ROUTES = {};
const SERVER = http.createServer((req, res) => {
  req.on("data", () => {});
  req.on("end", () => {
    const path = req.url.replace(/^\/api\/v1/, "").split("?")[0];
    const body = Object.prototype.hasOwnProperty.call(ROUTES, path) ? ROUTES[path] : {};
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(body));
  });
});

const PORT = await new Promise((r) => SERVER.listen(0, "127.0.0.2", () => r(SERVER.address().port)));
process.env.AIFY_SERVER_URL = `http://127.0.0.2:${PORT}`;
process.env.AIFY_API_KEY = "test-key";

const { createManagedOwnershipReader } = await import("../managed-ownership.mjs");
const { BRIDGE_INSTANCE_ID } = await import("../bridge-instance.mjs");

test.after(() => SERVER.close());

const ROOT = "/work/envA";

// The environment this bridge speaks for. cwdRoots is set explicitly: `workspaceWithinRoots` treats an
// EMPTY root list as "no restriction" and "/" as match-all, so a test that left it empty would assert the
// membership guard while actually bypassing it.
function reader(environment = { id: "envA", cwdRoots: [ROOT] }) {
  return createManagedOwnershipReader({ effectiveEnvironmentPayload: () => environment });
}

function serve({ agents = {}, sessions = [], environments = [] }) {
  ROUTES = {
    "/agents": { agents },
    "/sessions": { sessions },
    "/environments": { environments },
  };
}

const managed = (extra = {}) => ({
  sessionMode: "managed",
  cwd: `${ROOT}/proj`,
  runtimeState: { environmentId: "envA", bridgeInstanceId: BRIDGE_INSTANCE_ID },
  ...extra,
});

test("only MANAGED agents are candidates at all", async () => {
  serve({
    agents: {
      m1: managed(),
      r1: managed({ sessionMode: "resident" }),
      u1: managed({ sessionMode: undefined }),
    },
  });
  const records = await reader()();
  assert.deepEqual(records.map((r) => r.agentId), ["m1"]);
});

test("an agent belonging to ANOTHER environment is excluded", async () => {
  // The whole point of the read. Without this, a teardown sweep reaches into another environment.
  serve({
    agents: {
      mine: managed(),
      theirs: managed({ runtimeState: { environmentId: "envB", bridgeInstanceId: BRIDGE_INSTANCE_ID } }),
    },
  });
  const records = await reader()();
  assert.deepEqual(records.map((r) => r.agentId), ["mine"]);
});

test("a live SESSION in this environment proves membership even with no environmentId on the agent", async () => {
  // Sessions are queried scoped to this environment, so having one IS the membership evidence. An agent
  // that has not yet written runtimeState.environmentId would otherwise be invisible to the sweep.
  serve({
    agents: { m1: managed({ runtimeState: { bridgeInstanceId: BRIDGE_INSTANCE_ID } }) },
    sessions: [{ agentId: "m1", workspace: `${ROOT}/proj` }],
  });
  const records = await reader()();
  assert.deepEqual(records.map((r) => r.agentId), ["m1"]);
});

test("a workspace OUTSIDE the environment's roots is excluded", async () => {
  // The second membership guard, and the one that matters when two environments share a service.
  serve({
    agents: {
      inside: managed({ cwd: `${ROOT}/proj` }),
      outside: managed({ cwd: "/somewhere/else" }),
    },
  });
  const records = await reader()();
  assert.deepEqual(records.map((r) => r.agentId), ["inside"]);
});

test("the session's workspace decides membership, overriding the agent's recorded cwd", async () => {
  // `session?.workspace || info.cwd || DEFAULT_CWD`. A managed agent whose SESSION runs outside the roots
  // is not ours to reap, however inviting its stale `cwd` field looks.
  serve({
    agents: { drifted: managed({ cwd: `${ROOT}/proj` }) },
    sessions: [{ agentId: "drifted", workspace: "/somewhere/else" }],
  });
  assert.deepEqual(await reader()(), []);
});

test("the FIRST session for an agent wins, not the last", async () => {
  // `if (!sessionByAgent.has(agentId))`. /sessions returns newest-first, so the first row is the live one;
  // taking the last would judge membership from a dead session's workspace.
  serve({
    agents: { m1: managed({ cwd: "/somewhere/else" }) },
    sessions: [
      { agentId: "m1", workspace: `${ROOT}/live` },
      { agentId: "m1", workspace: "/somewhere/else" },
    ],
  });
  assert.deepEqual((await reader()()).map((r) => r.agentId), ["m1"]);
});

test("ownerLive is TRUE for our own bridge — we do not reap what we own", async () => {
  serve({ agents: { m1: managed() } });
  const [record] = await reader()();
  assert.equal(record.owningBridgeId, BRIDGE_INSTANCE_ID);
  assert.equal(record.ownerLive, true);
});

test("an agent with NO owning bridge is not live, so it is eligible", async () => {
  // Never synced / unknown owner. Reported eligible rather than protected, which is the safe direction for
  // a survivor of a crashed predecessor — the case this sweep exists for.
  serve({ agents: { m1: managed({ runtimeState: { environmentId: "envA" } }) } });
  const [record] = await reader()();
  assert.equal(record.owningBridgeId, "");
  assert.equal(record.ownerLive, false);
});

test("another bridge counts as live only while its environment is ONLINE", async () => {
  const other = "bridge-other";
  const agents = { m1: managed({ runtimeState: { environmentId: "envA", bridgeInstanceId: other } }) };

  serve({ agents, environments: [{ bridgeId: other, status: "online" }] });
  assert.equal((await reader()())[0].ownerLive, true, "an online owner must be protected");

  serve({ agents, environments: [{ bridgeId: other, status: "offline" }] });
  assert.equal((await reader()())[0].ownerLive, false, "an offline owner leaves its agents eligible");

  serve({ agents, environments: [] });
  assert.equal((await reader()())[0].ownerLive, false, "an owner that is not listed at all is not live");
});

test("a malformed or empty service response yields no candidates instead of throwing", async () => {
  // A sweep that throws here leaves the previous state in place; one that invents candidates reaps. Both
  // failures are worse than returning nothing, and the optional chaining is load-bearing.
  ROUTES = { "/agents": {}, "/sessions": {}, "/environments": {} };
  assert.deepEqual(await reader()(), []);

  ROUTES = { "/agents": { agents: null }, "/sessions": { sessions: null }, "/environments": { environments: "nope" } };
  assert.deepEqual(await reader()(), []);
});

test("the injected payload is read on EVERY call, not captured once", async () => {
  // THE REASON IT IS INJECTED AS A FUNCTION. `effectiveEnvironmentPayload` reads `remoteEffectiveCwdRoots`,
  // which `heartbeatEnvironment` rewrites when the service reports different roots. Capturing the value at
  // construction would pin this bridge to the roots it booted with, and it would keep judging membership
  // from them for the life of the process.
  let environment = { id: "envA", cwdRoots: ["/first"] };
  let calls = 0;
  const read = createManagedOwnershipReader({
    effectiveEnvironmentPayload: () => {
      calls += 1;
      return environment;
    },
  });

  serve({ agents: { m1: managed({ cwd: "/second/proj" }) } });
  assert.deepEqual(await read(), [], "with the first roots the agent is outside");

  environment = { id: "envA", cwdRoots: ["/second"] };
  assert.deepEqual((await read()).map((r) => r.agentId), ["m1"], "the new roots must take effect");
  assert.equal(calls, 2, "the payload must be read once per call");
});
