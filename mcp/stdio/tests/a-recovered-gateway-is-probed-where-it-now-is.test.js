// A gateway the delivery loop recovered is probed where it NOW is, not where the first one was (0.9.10).
//
// The liveness probe took its index URL from the gateway at bring-up, and recovery replaced the gateway
// without replacing the probe. Until 0.9.10 the port was fixed per agent, so the stale URL was the same
// URL. A hand launch with no aify-env now recovers onto a free port the OS gives, and the probe kept
// asking the dead old port and reported the healthy replacement as lost (review of f1a56284, reproduced
// with this harness: one false resident-lost).
//
// The real delivery loop runs with fake HTTP and WebSocket collaborators and captured interval timers.
// Controls on both sides: a same-port recovery stays live, and a dead replacement is still reported.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const home = fs.mkdtempSync(path.join(os.tmpdir(), "aify-recovered-gateway-"));
process.env.AIFY_HERMES_GATEWAY_PROBE_MS = "5000";
process.env.AIFY_HERMES_GATEWAY_PROBE_THRESHOLD = "1";
const { runDeliveryLoop } = await import("../hermes-delivery-loop.mjs");

const gateway = (port) => ({ port, token: "fixture", wsUrl: `ws://127.0.0.1:${port}/api/ws?token=fixture`, child: null });

/** One bring-up on 19001, one refused connect, one recovery onto `nextPort`, then one liveness tick. */
async function lostReportsAfterRecovery({ nextPort, nextAlive }) {
  const realSetInterval = globalThis.setInterval;
  const realClearInterval = globalThis.clearInterval;
  const timers = [];
  globalThis.setInterval = (tick, ms) => { const timer = { tick, ms, unref() {} }; timers.push(timer); return timer; };
  globalThis.clearInterval = () => {};
  let recovered = false;
  let opens = 0;
  let ticked = false;
  const calls = [];
  try {
    await runDeliveryLoop("recovered-gateway-fixture", {
      launchedGateway: gateway(19001),
      serverUrl: "http://fixture.invalid",
      markerDir: home,
      maxIterations: 2,
      gatewaySource: async () => { recovered = true; return gateway(nextPort); },
      fetchImpl: async (url) => ({ ok: recovered && Number(new URL(url).port) === nextPort && nextAlive }),
      openWs: async (url) => {
        opens += 1;
        if (opens === 1) throw Object.assign(new Error("fixture connection refused"), { code: "ECONNREFUSED" });
        assert.equal(new URL(url).port, String(nextPort));
        return { request: async () => ({ sessions: [] }), close() {} };
      },
      httpCall: async (method, endpoint, body) => { calls.push({ method, endpoint, body }); return { ok: true, run: null }; },
      startLivenessHeartbeat: () => () => {},
      startResumeSync: () => {},
      startEffort: () => () => {},
      installTeardown: () => {},
      writeReady: () => {},
      clearReady: () => {},
      sleepImpl: async () => {
        if (!recovered || ticked) return;
        ticked = true;
        const probe = timers.find((t) => t.ms === 5000);
        assert.ok(probe, "the liveness probe's timer was armed");
        await probe.tick();
      },
    });
  } finally {
    globalThis.setInterval = realSetInterval;
    globalThis.clearInterval = realClearInterval;
  }
  assert.equal(opens, 2, "control: one refused connect, then the recovered gateway");
  assert.equal(ticked, true, "control: the probe ticked after the recovery");
  return calls.filter((call) => call.endpoint.endsWith("/resident-lost")).length;
}

test.after(() => fs.rmSync(home, { recursive: true, force: true }));

test("a healthy gateway recovered onto a NEW port is not reported lost", async () => {
  assert.equal(await lostReportsAfterRecovery({ nextPort: 19002, nextAlive: true }), 0);
});

test("control: a healthy gateway recovered onto the SAME port is not reported lost", async () => {
  assert.equal(await lostReportsAfterRecovery({ nextPort: 19001, nextAlive: true }), 0);
});

test("control: a recovered gateway that does not answer IS reported lost", async () => {
  assert.equal(await lostReportsAfterRecovery({ nextPort: 19002, nextAlive: false }), 1);
});
