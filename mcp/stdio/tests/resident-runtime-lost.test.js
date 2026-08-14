// Reporting a lost resident runtime — and standing the bridge down when its last agent goes with it.
//
// Extracted from server.js in v0.5.4, where nothing could reach it.
//
// THE `finally` IS THE WHOLE POINT, and it runs on BOTH paths. Reporting-then-forgetting only on
// success would leave a bridge holding an agent whose runtime is gone every time the service was
// briefly unreachable — and it would go on claiming that agent's work. This suite drives both.

import assert from "node:assert/strict";
import test from "node:test";
import http from "node:http";

const REQUESTS = [];
let STATUS = 200;
let PAYLOAD = {};
const SERVER = http.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", () => {
    REQUESTS.push({ method: req.method, url: req.url, body });
    res.writeHead(STATUS, { "content-type": "application/json" });
    res.end(JSON.stringify(PAYLOAD));
  });
});
const PORT = await new Promise((r) => SERVER.listen(0, "127.0.0.2", () => r(SERVER.address().port)));

process.env.AIFY_SERVER_URL = `http://127.0.0.2:${PORT}`;
process.env.AIFY_API_KEY = "test-key";
const { reportResidentRuntimeLost } = await import("../resident-runtime-lost.mjs");
const { REMOTE_AGENT_STATE } = await import("../bridge-agent-state.mjs");

test.after(() => SERVER.close());

function scenario({ status = 200, payload = {}, agents = [] } = {}) {
  REQUESTS.length = 0;
  STATUS = status;
  PAYLOAD = payload;
  REMOTE_AGENT_STATE.clear();
  for (const id of agents) REMOTE_AGENT_STATE.set(id, { info: { id } });
}

const deps = (onShutdown = () => {}) => ({ MACHINE_ID: "machine-1", shutdownWithStatus: onShutdown });

test("it POSTs resident-lost for the ENCODED agent, with this bridge and machine", async () => {
  scenario({ agents: ["a/b c"] });
  await reportResidentRuntimeLost("a/b c", {}, "gone", deps());
  const post = REQUESTS.find((r) => r.method === "POST");
  assert.ok(post);
  assert.match(post.url, /\/agents\/a%2Fb(%20|\+)c\/resident-lost$/);
  const sent = JSON.parse(post.body);
  assert.equal(sent.machineId, "machine-1");
  assert.equal(sent.reason, "gone");
});

test("the agent's OWN machineId wins over this bridge's, when it has one", async () => {
  // A resident agent may be bound to a different machine than the bridge reporting for it; sending the
  // bridge's id would attribute the loss to the wrong host.
  scenario({ agents: ["a"] });
  await reportResidentRuntimeLost("a", { machineId: "their-machine" }, "gone", deps());
  assert.equal(JSON.parse(REQUESTS[0].body).machineId, "their-machine");
});

test("THE AGENT IS FORGOTTEN EVEN WHEN THE REPORT FAILS", async () => {
  // The `finally`. On a 500 the bridge must still stop claiming for an agent whose runtime is gone —
  // otherwise a brief service outage leaves it claiming work it cannot run.
  scenario({ status: 500, agents: ["a", "b"] });
  await assert.doesNotReject(() => reportResidentRuntimeLost("a", {}, "gone", deps()));
  assert.equal(REMOTE_AGENT_STATE.has("a"), false, "forgotten despite the failure");
  assert.equal(REMOTE_AGENT_STATE.has("b"), true, "and only that agent");
});

test("…and when it succeeds", async () => {
  scenario({ agents: ["a", "b"] });
  await reportResidentRuntimeLost("a", {}, "gone", deps());
  assert.equal(REMOTE_AGENT_STATE.has("a"), false);
  assert.equal(REMOTE_AGENT_STATE.has("b"), true);
});

test("A RESIDENT BRIDGE STANDS DOWN when its LAST agent is forgotten", async () => {
  // A resident bridge with no agents has nothing left to host. The exit is deferred 50ms and unref'd so
  // the report can flush first, which is why this awaits before asserting.
  scenario({ agents: ["only"] });
  let stopped = 0;
  await reportResidentRuntimeLost("only", {}, "gone", deps(() => { stopped += 1; }));
  assert.equal(stopped, 0, "not synchronous — the report must flush first");
  await new Promise((r) => setTimeout(r, 120));
  assert.equal(stopped, 1);
});

test("it does NOT stand down while other agents remain", async () => {
  scenario({ agents: ["a", "b"] });
  let stopped = 0;
  await reportResidentRuntimeLost("a", {}, "gone", deps(() => { stopped += 1; }));
  await new Promise((r) => setTimeout(r, 120));
  assert.equal(stopped, 0, "one agent lost is not the end of the bridge");
});

test("a missing reason falls back to the documented default", async () => {
  // The default parameter survived the extraction — server.js's shim passes `reason` through, so an
  // omitted argument must still reach the service as the stated reason rather than as `undefined`.
  scenario({ agents: ["a"] });
  await reportResidentRuntimeLost("a", {}, undefined, deps());
  assert.equal(JSON.parse(REQUESTS[0].body).reason, "resident runtime app-server is unreachable");
});
