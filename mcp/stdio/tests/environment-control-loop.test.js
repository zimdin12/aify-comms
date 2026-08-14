// The environment-control claim pass, tested by CALLING it against a real HTTP server.
//
// Extracted from server.js in v0.5.4, where nothing could reach it. This is the path that makes a
// SUPERSEDED bridge stand down: when a newer bridge takes over an environment, the service hands this
// one a `stop` control and it exits. Getting that wrong in either direction is an incident this repo
// has already had — an older bridge that refuses to go keeps claiming work the new one should own, and
// one that goes on the WRONG signal takes down a healthy fleet.
//
// `shutdownWithStatus` is injected rather than imported, so the test observes the decision to stop
// without the test runner being stopped. That is also why the module does not own it: something that
// can end the process is better handed in than reached for.
//
// Fake service on 127.0.0.2, `AIFY_SERVER_URL` set BEFORE the import — aify-service-endpoint.mjs reads
// it at module load, once per process, and ESM bindings cannot be monkey-patched.

import assert from "node:assert/strict";
import test from "node:test";
import http from "node:http";

const REQUESTS = [];
let CONTROL = null;
const SERVER = http.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", () => {
    REQUESTS.push({ method: req.method, url: req.url, body });
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(req.url.endsWith("/claim") ? { control: CONTROL } : {}));
  });
});
const PORT = await new Promise((r) => SERVER.listen(0, "127.0.0.2", () => r(SERVER.address().port)));

process.env.AIFY_SERVER_URL = `http://127.0.0.2:${PORT}`;
process.env.AIFY_API_KEY = "test-key";
const { runEnvironmentControlPass } = await import("../environment-control-loop.mjs");
const { BRIDGE_INSTANCE_ID } = await import("../bridge-instance.mjs");

test.after(() => SERVER.close());

function deps(overrides = {}) {
  return {
    CLAIM_OPTS: {},
    CLAIM_WAIT_MS: 0,
    MACHINE_ID: "machine-1",
    effectiveEnvironmentPayload: () => ({ id: "env-1" }),
    shutdownWithStatus: overrides.shutdownWithStatus ?? (() => {}),
  };
}

function scenario(control) {
  REQUESTS.length = 0;
  CONTROL = control;
}

test("it claims against THIS environment and bridge", async () => {
  // The claim is scoped by environment and bridge id. A wrong scope either claims another
  // environment's controls or never sees its own.
  scenario(null);
  await runEnvironmentControlPass(deps());
  const claim = REQUESTS.find((r) => r.url.endsWith("/environments/controls/claim"));
  assert.ok(claim, "a claim must be attempted");
  const sent = JSON.parse(claim.body);
  assert.equal(sent.environmentId, "env-1");
  assert.equal(sent.bridgeId, BRIDGE_INSTANCE_ID);
  assert.equal(sent.machineId, "machine-1");
});

test("NO CONTROL means no further requests — the common case, every poll", async () => {
  // This runs on a timer forever. An empty claim must cost exactly one request.
  scenario(null);
  await runEnvironmentControlPass(deps());
  assert.equal(REQUESTS.length, 1, "an idle poll must not PATCH anything");
});

test("A SUPERSEDE STOP shuts this bridge down, and reports the control completed first", async () => {
  // The path that matters. The PATCH must go out BEFORE the exit is scheduled, or the service is left
  // with a control that never completes and will hand it to the next bridge too.
  let stopped = 0;
  scenario({
    id: "ctl-1",
    action: "stop",
    requestedBy: "server:superseded-bridge",
    currentEnvironment: { bridgeId: "a-newer-bridge", metadata: { pid: 42, cwd: "/w" } },
  });
  await runEnvironmentControlPass(deps({ shutdownWithStatus: () => { stopped += 1; } }));

  const patch = REQUESTS.find((r) => r.method === "PATCH");
  assert.ok(patch, "the control must be reported completed");
  assert.match(patch.url, /\/environments\/controls\/ctl-1$/);
  assert.equal(JSON.parse(patch.body).status, "completed");

  // The exit is deliberately deferred by 50ms so the PATCH lands first.
  assert.equal(stopped, 0, "shutdown must not be synchronous");
  await new Promise((r) => setTimeout(r, 120));
  assert.equal(stopped, 1, "…but it must happen");
});

test("a plain stop — not a supersede — also stops the bridge", async () => {
  // The `requestedBy` branch only changes the log line. Both arms must exit, or an operator-requested
  // environment stop would be ignored while a supersede was honoured.
  let stopped = 0;
  scenario({ id: "ctl-2", action: "stop", currentEnvironment: {} });
  await runEnvironmentControlPass(deps({ shutdownWithStatus: () => { stopped += 1; } }));
  await new Promise((r) => setTimeout(r, 120));
  assert.equal(stopped, 1);
});

test("a stop naming THIS bridge as the replacement is still a stop", async () => {
  // `current.bridgeId !== BRIDGE_INSTANCE_ID` guards only the wording. If it gated the exit, a bridge
  // could be told to stand down and simply not.
  let stopped = 0;
  scenario({
    id: "ctl-3",
    action: "stop",
    requestedBy: "server:superseded-bridge",
    currentEnvironment: { bridgeId: BRIDGE_INSTANCE_ID },
  });
  await runEnvironmentControlPass(deps({ shutdownWithStatus: () => { stopped += 1; } }));
  await new Promise((r) => setTimeout(r, 120));
  assert.equal(stopped, 1);
});

test("an UNKNOWN action is reported failed and does NOT stop the bridge", async () => {
  // The default arm. A control this bridge cannot perform must be handed back as failed rather than
  // left claimed — and must certainly not be treated as a stop.
  let stopped = 0;
  scenario({ id: "ctl-4", action: "something-new" });
  await runEnvironmentControlPass(deps({ shutdownWithStatus: () => { stopped += 1; } }));
  const patch = REQUESTS.find((r) => r.method === "PATCH");
  assert.ok(patch);
  assert.equal(JSON.parse(patch.body).status, "failed");
  await new Promise((r) => setTimeout(r, 120));
  assert.equal(stopped, 0, "an unknown action must never stop the bridge");
});
