import assert from "node:assert/strict";
import { test } from "node:test";
import {
  gatewayProbeShouldDeclareDead,
  nextConsecutiveFailures,
  startGatewayLivenessProbe,
  DEFAULT_GATEWAY_PROBE_THRESHOLD,
} from "../hermes-gateway-liveness.js";
import {
  gatewayIndexUrlFromWs,
  makeGatewayReachabilityProbe,
} from "../hermes-managed-host.js";
import {
  makeApiServerLivenessProbe,
  startApiServerGatewayProbe,
} from "../hermes-channel.js";

// --- pure decision: gatewayProbeShouldDeclareDead -------------------------

test("gatewayProbeShouldDeclareDead is false below the threshold", () => {
  assert.equal(gatewayProbeShouldDeclareDead(0, 3), false);
  assert.equal(gatewayProbeShouldDeclareDead(1, 3), false);
  assert.equal(gatewayProbeShouldDeclareDead(2, 3), false);
});

test("gatewayProbeShouldDeclareDead is true at and above the threshold", () => {
  assert.equal(gatewayProbeShouldDeclareDead(3, 3), true);
  assert.equal(gatewayProbeShouldDeclareDead(4, 3), true);
});

test("gatewayProbeShouldDeclareDead defaults the threshold to 3", () => {
  assert.equal(DEFAULT_GATEWAY_PROBE_THRESHOLD, 3);
  assert.equal(gatewayProbeShouldDeclareDead(2), false);
  assert.equal(gatewayProbeShouldDeclareDead(3), true);
});

test("gatewayProbeShouldDeclareDead is false for a non-positive/invalid threshold", () => {
  assert.equal(gatewayProbeShouldDeclareDead(5, 0), false);
  assert.equal(gatewayProbeShouldDeclareDead(5, -1), false);
  assert.equal(gatewayProbeShouldDeclareDead(5, NaN), false);
});

// --- pure counter logic: nextConsecutiveFailures --------------------------

test("nextConsecutiveFailures increments on failure, resets on success", () => {
  let n = 0;
  n = nextConsecutiveFailures(n, false); // fail
  assert.equal(n, 1);
  n = nextConsecutiveFailures(n, false); // fail
  assert.equal(n, 2);
  n = nextConsecutiveFailures(n, true); // success → reset
  assert.equal(n, 0);
  n = nextConsecutiveFailures(n, false); // fail again
  assert.equal(n, 1);
});

// A single slow/transient probe (one failure) followed by a success must NOT
// reach the threshold — this is the anti-flapping guarantee.
test("a transient single failure followed by success never reaches threshold", () => {
  let n = 0;
  const seq = [false, true, false, true, false, true];
  let everDead = false;
  for (const ok of seq) {
    n = nextConsecutiveFailures(n, ok);
    if (gatewayProbeShouldDeclareDead(n, 3)) everDead = true;
  }
  assert.equal(everDead, false, "alternating fail/success must never declare dead");
});

test("3 consecutive failures reaches the threshold; a recovery before that does not", () => {
  let n = 0;
  // fail, fail, success (recovery) → counter resets, never declared dead
  for (const ok of [false, false, true]) n = nextConsecutiveFailures(n, ok);
  assert.equal(gatewayProbeShouldDeclareDead(n, 3), false);
  // now three straight failures
  for (const ok of [false, false, false]) n = nextConsecutiveFailures(n, ok);
  assert.equal(gatewayProbeShouldDeclareDead(n, 3), true);
});

// --- driver: startGatewayLivenessProbe ------------------------------------

// Helper: poll a condition with a REF'd setTimeout so the event loop stays
// alive while we wait for the (unref'd) probe interval to drive a result. A
// pending promise alone does NOT keep the loop alive; this ref'd poll does.
async function waitFor(cond, { timeoutMs = 1000, stepMs = 5 } = {}) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (cond()) return true;
    if (Date.now() >= deadline) return false;
    await new Promise((r) => setTimeout(r, stepMs));
  }
}

test("reportDead fires exactly once after N consecutive failures (latched)", async () => {
  const reportCalls = [];
  let calls = 0;
  const stop = startGatewayLivenessProbe({
    intervalMs: 5,
    threshold: 3,
    probe: async () => {
      calls += 1;
      return { alive: false }; // always dead
    },
    reportDead: async (info) => {
      reportCalls.push(info);
    },
  });
  const fired = await waitFor(() => reportCalls.length >= 1);
  assert.ok(fired, "reportDead should fire within the timeout");
  // give extra ticks a chance to (wrongly) double-report
  await new Promise((r) => setTimeout(r, 40));
  stop();
  assert.equal(reportCalls.length, 1, `reportDead must fire exactly once; got ${reportCalls.length}`);
  assert.ok(calls >= 3, `expected >=3 probes before declaring dead; got ${calls}`);
});

test("reportDead does NOT fire on 1 or 2 failures", async () => {
  let probes = 0;
  const reportCalls = [];
  const stop = startGatewayLivenessProbe({
    intervalMs: 5,
    threshold: 3,
    // alive after 2 failures so the counter never reaches 3
    probe: async () => {
      probes += 1;
      return { alive: probes > 2 };
    },
    reportDead: async (info) => reportCalls.push(info),
  });
  await new Promise((r) => setTimeout(r, 60));
  stop();
  assert.equal(reportCalls.length, 0, "must not report dead when a success arrives before threshold");
});

test("a recovery (success) resets the counter so reportDead never fires", async () => {
  // fail, fail, success, fail, fail, success, ... → never 3 in a row
  const pattern = [false, false, true];
  let i = 0;
  const reportCalls = [];
  const stop = startGatewayLivenessProbe({
    intervalMs: 5,
    threshold: 3,
    probe: async () => {
      const ok = pattern[i % pattern.length];
      i += 1;
      return { alive: ok };
    },
    reportDead: async (info) => reportCalls.push(info),
  });
  await new Promise((r) => setTimeout(r, 80));
  stop();
  assert.equal(reportCalls.length, 0, "alternating fail/fail/success must never declare dead");
});

test("a throwing probe counts as a failure and never crashes the timer", async () => {
  let probes = 0;
  const reportCalls = [];
  const stop = startGatewayLivenessProbe({
    intervalMs: 5,
    threshold: 3,
    probe: async () => {
      probes += 1;
      throw new Error("boom"); // throw == failure
    },
    reportDead: async (info) => {
      reportCalls.push(info);
    },
  });
  const fired = await waitFor(() => reportCalls.length >= 1);
  assert.ok(fired, "throwing probe should still declare dead");
  await new Promise((r) => setTimeout(r, 30));
  stop();
  assert.equal(reportCalls.length, 1, "throwing probe should still declare dead exactly once");
  assert.ok(probes >= 3, "timer kept ticking despite throws");
});

test("a healthy gateway never reports dead", async () => {
  const reportCalls = [];
  const stop = startGatewayLivenessProbe({
    intervalMs: 5,
    threshold: 3,
    probe: async () => ({ alive: true }),
    reportDead: async (info) => reportCalls.push(info),
  });
  await new Promise((r) => setTimeout(r, 60));
  stop();
  assert.equal(reportCalls.length, 0, "a healthy gateway must stay alive");
});

test("stop() halts probing", async () => {
  let probes = 0;
  const stop = startGatewayLivenessProbe({
    intervalMs: 5,
    threshold: 3,
    probe: async () => {
      probes += 1;
      return { alive: true };
    },
    reportDead: async () => {},
  });
  await new Promise((r) => setTimeout(r, 30));
  stop();
  const after = probes;
  await new Promise((r) => setTimeout(r, 40));
  assert.equal(probes, after, "no probes after stop()");
});

test("invalid config returns a no-op stop (no probe, no throw)", async () => {
  let probes = 0;
  const stop = startGatewayLivenessProbe({
    intervalMs: 0, // invalid
    probe: async () => {
      probes += 1;
      return { alive: false };
    },
    reportDead: async () => {},
  });
  assert.equal(typeof stop, "function");
  await new Promise((r) => setTimeout(r, 30));
  stop();
  assert.equal(probes, 0, "invalid intervalMs must not start a probe");
});

test("reportDead is not re-armed after latch even if probe later recovers then dies again", async () => {
  // Once latched, we never re-report within this probe's lifetime (a single
  // resident-lost transition is enough; the server owns the state).
  const reportCalls = [];
  let phase = 0;
  const stop = startGatewayLivenessProbe({
    intervalMs: 5,
    threshold: 3,
    probe: async () => {
      phase += 1;
      // dead for first 3, then alive, then dead again
      if (phase <= 3) return { alive: false };
      if (phase <= 5) return { alive: true };
      return { alive: false };
    },
    reportDead: async (info) => {
      reportCalls.push(info);
    },
  });
  const fired = await waitFor(() => reportCalls.length >= 1);
  assert.ok(fired, "reportDead should fire once after the initial 3 failures");
  await new Promise((r) => setTimeout(r, 80));
  stop();
  assert.equal(reportCalls.length, 1, "latched: reportDead fires at most once for the probe lifetime");
});

// --- gatewayIndexUrlFromWs (managed gateway HTTP index) -------------------

test("gatewayIndexUrlFromWs derives the http index from a ws gateway url", () => {
  assert.equal(
    gatewayIndexUrlFromWs("ws://127.0.0.1:8800/api/ws?token=abc123"),
    "http://127.0.0.1:8800/",
  );
  assert.equal(
    gatewayIndexUrlFromWs("wss://127.0.0.1:9001/api/ws?token=x"),
    "https://127.0.0.1:9001/",
  );
});

test("gatewayIndexUrlFromWs returns '' for empty/garbage input", () => {
  assert.equal(gatewayIndexUrlFromWs(""), "");
  assert.equal(gatewayIndexUrlFromWs(null), "");
  assert.equal(gatewayIndexUrlFromWs("not a url"), "");
});

// --- makeGatewayReachabilityProbe (managed: dashboard index) --------------

test("makeGatewayReachabilityProbe reports alive on an OK response", async () => {
  const probe = makeGatewayReachabilityProbe({
    indexUrl: "http://127.0.0.1:8800/",
    fetchImpl: async () => ({ ok: true }),
  });
  assert.deepEqual(await probe(), { alive: true });
});

test("makeGatewayReachabilityProbe reports not-alive when fetch throws (ECONNREFUSED)", async () => {
  const probe = makeGatewayReachabilityProbe({
    indexUrl: "http://127.0.0.1:8800/",
    fetchImpl: async () => {
      const e = new Error("connect ECONNREFUSED");
      e.code = "ECONNREFUSED";
      throw e;
    },
  });
  assert.deepEqual(await probe(), { alive: false });
});

test("makeGatewayReachabilityProbe reports not-alive when no index url", async () => {
  const probe = makeGatewayReachabilityProbe({ indexUrl: "", fetchImpl: async () => ({ ok: true }) });
  assert.deepEqual(await probe(), { alive: false });
});

// --- makeApiServerLivenessProbe (resident/api_server: /health) ------------

test("makeApiServerLivenessProbe maps probeApiServer.available → alive", async () => {
  const aliveProbe = makeApiServerLivenessProbe({
    baseUrl: "http://127.0.0.1:8642",
    key: "k",
    probe: async () => ({ available: true, version: "1.2.3" }),
  });
  assert.deepEqual(await aliveProbe(), { alive: true });

  const deadProbe = makeApiServerLivenessProbe({
    baseUrl: "http://127.0.0.1:8642",
    key: "k",
    probe: async () => ({ available: false, reason: "daemon not running" }),
  });
  assert.deepEqual(await deadProbe(), { alive: false });
});

// --- startApiServerGatewayProbe (resident wiring → reportGatewayDead) ------

test("startApiServerGatewayProbe reports dead once after N api_server failures (resident-lost, no bridgeId)", async () => {
  const reportCalls = [];
  const stop = startApiServerGatewayProbe({
    agentId: "agent-x",
    baseUrl: "http://127.0.0.1:8642",
    key: "k",
    httpCall: async () => ({}),
    serverUrl: "http://127.0.0.1:8800",
    intervalMs: 5,
    threshold: 3,
    probe: async () => ({ available: false, reason: "daemon not running" }),
    reportDeadImpl: async (info) => {
      reportCalls.push(info);
    },
  });
  const fired = await waitFor(() => reportCalls.length >= 1);
  assert.ok(fired, "reportDead should fire after 3 api_server probe failures");
  await new Promise((r) => setTimeout(r, 40));
  stop();
  assert.equal(reportCalls.length, 1, "reportGatewayDead must fire exactly once");
  // The reused reportGatewayDead path is fed agentId + gatewayUrl, NEVER a
  // bridgeId (resident bridge id differs from this sidecar; bridgeId would be
  // rejected by the server's bridge_not_current guard).
  assert.equal(reportCalls[0].agentId, "agent-x");
  assert.equal(reportCalls[0].gatewayUrl, "http://127.0.0.1:8642");
  assert.equal(reportCalls[0].bridgeId, undefined, "must not send a bridgeId");
});

test("startApiServerGatewayProbe stays alive for a healthy api_server", async () => {
  const reportCalls = [];
  const stop = startApiServerGatewayProbe({
    agentId: "agent-y",
    baseUrl: "http://127.0.0.1:8642",
    key: "k",
    httpCall: async () => ({}),
    serverUrl: "http://127.0.0.1:8800",
    intervalMs: 5,
    threshold: 3,
    probe: async () => ({ available: true }),
    reportDeadImpl: async (info) => reportCalls.push(info),
  });
  await new Promise((r) => setTimeout(r, 50));
  stop();
  assert.equal(reportCalls.length, 0, "a healthy api_server must never report dead");
});
