// Historical startup logs appeared at 57.6-71.9 seconds. These closed clock,
// process and protocol fixtures are not a live-agent batch.
import "./_sealed-temp.mjs";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { test } from "node:test";

// This file certifies the default, independently of an operator's explicit override.
delete process.env.AIFY_HERMES_GATEWAY_READY_MS;
const { ensureGatewayHost } = await import("../hermes-gateway.mjs");
const { runEnsureHostCli } = await import("../hermes-managed-host.js");
const realFetch = globalThis.fetch;
globalThis.fetch = async () => { throw new Error("unconfigured fixture fetch"); };
process.on("exit", () => { globalThis.fetch = realFetch; });

function boot(t, { servesAt = 71900, readyTimeoutMs, cli = false, failWs = false,
  gateway = ensureGatewayHost, unknownLifetime = false, exitAtFetch = false,
  exitAtWs = false, hangFetch = false } = {}) {
  let clock = 0;
  let spawned = false;
  let attempts = 0;
  let wsProbes = 0;
  let wsClosed = 0;
  const children = [];
  const output = [];
  const signals = [];
  t.mock.method(Date, "now", () => clock);
  const spawn = (_command, _args, options) => {
    assert.equal(options.windowsHide, true);
    assert.equal(options.detached, true);
    const child = new EventEmitter();
    child.pid = 400000 + children.length;
    child.unref = () => {};
    child.stderr = new EventEmitter();
    if (!unknownLifetime) { child.exitCode = null; child.signalCode = null; }
    children.push(child);
    spawned = true;
    return child;
  };
  const exit = () => {
    const child = children[0];
    child.stderr.emit("data", "booting\nfixture exit detail token=fixture-secret\n");
    child.exitCode = 23;
    child.emit("exit", 23, null);
  };
  const fetchImpl = async (url, options) => {
    assert.match(url, /^http:\/\/127\.0\.0\.1:\d+\/$/);
    assert.equal(options.method, "GET");
    if (!spawned) throw new Error("ECONNREFUSED");
    // Two failed probes, including one beyond the former 60-second default,
    // before a successful index. Each launch owns its own deadline and token.
    attempts++;
    signals.push(options.signal);
    if (exitAtFetch) exit();
    if (exitAtFetch || hangFetch) return new Promise(() => {});
    clock = attempts === 1 ? Math.min(servesAt / 2, 58000)
      : attempts === 2 ? servesAt - 1000 : servesAt;
    if (attempts < 3) throw new Error("ECONNREFUSED");
    return { ok: true, status: 200, text: async () => '<script>__HERMES_SESSION_TOKEN__="fixture-session"</script>' };
  };
  const openWsImpl = async (url) => {
    assert.match(url, /^ws:\/\/127\.0\.0\.1:\d+\/api\/ws\?token=fixture-session$/);
    wsProbes++;
    if (exitAtWs) { exit(); return new Promise(() => {}); }
    if (failWs) throw new Error("fixture WS refused");
    return { close() { wsClosed++; } };
  };
  const promise = cli
    ? runEnsureHostCli("batch-fixture", { spawnImpl: spawn, fetchImpl, openWsImpl,
      attach: () => {}, out: (line) => output.push(line), err: () => {} })
    : gateway({ agentId: "batch-fixture", port: 9397, spawn, fetchImpl,
      openWsImpl, probeFirst: false, readyIntervalMs: 1,
      ...(readyTimeoutMs === undefined ? {} : { readyTimeoutMs }) });
  return { promise, children, output, signals, counts: () => ({ attempts, wsProbes, wsClosed, clock }) };
}

test("eight sequential live gateway starts survive slow boots beyond two minutes", async (t) => {
  const bootMilestones = [69800, 71900, 64800, 57600, 180000, 240000, 360000, 599000];
  for (const servesAt of bootMilestones) {
    const launch = boot(t, { servesAt });
    const host = await launch.promise;
    assert.equal(launch.children.length, 1, "a slow start must not spawn a replacement");
    assert.equal(host.child, launch.children[0]);
    assert.equal(host.reused, false);
    assert.deepEqual(launch.counts(), { attempts: 3, wsProbes: 1, wsClosed: 1, clock: servesAt });
    t.mock.restoreAll();
  }
});

test("the launcher route waits on its live gateway rather than the former timer", async (t) => {
  const launch = boot(t, { cli: true, servesAt: 180000 });
  const host = await launch.promise;
  assert.equal(host.token, "fixture-session");
  assert.equal(launch.children.length, 1);
  assert.equal(launch.counts().wsProbes, 1);
  assert.equal(launch.output.length, 1);
  assert.deepEqual(JSON.parse(launch.output[0]), host);
});

test("an explicit 60-second readiness limit still refuses a later boot", async (t) => {
  const launch = boot(t, { readyTimeoutMs: 60000 });
  await assert.rejects(launch.promise, /did not become ready within 60000ms/);
  assert.equal(launch.counts().attempts, 2);
  assert.equal(launch.counts().wsProbes, 0);
});

test("an operator's explicit environment limit still replaces the default", async (t) => {
  process.env.AIFY_HERMES_GATEWAY_READY_MS = "60000";
  let gateway;
  try {
    ({ ensureGatewayHost: gateway } = await import("../hermes-gateway.mjs?explicit-ready-limit"));
  } finally {
    delete process.env.AIFY_HERMES_GATEWAY_READY_MS;
  }
  const launch = boot(t, { gateway });
  await assert.rejects(launch.promise, /did not become ready within 60000ms/);
  assert.equal(launch.counts().attempts, 2);
  assert.equal(launch.counts().wsProbes, 0);
});

test("a live but hung child reaches the ten-minute outer ceiling", async (t) => {
  const launch = boot(t, { servesAt: 601100 });
  await assert.rejects(launch.promise, /did not become ready within 600000ms/);
  assert.equal(launch.counts().attempts, 2);
  assert.equal(launch.counts().wsProbes, 0);
});

test("a late index alone is still not readiness when its WebSocket refuses", async (t) => {
  const launch = boot(t, { failWs: true });
  await assert.rejects(launch.promise, /gateway \/api\/ws/);
  assert.equal(launch.counts().attempts, 3);
  assert.equal(launch.counts().wsProbes, 1);
});

async function fastFailure(promise) {
  let timer;
  const guard = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("fixture guard: child exit was not consumed")), 100);
  });
  try { return await Promise.race([promise, guard]); }
  finally { clearTimeout(timer); }
}

test("an exited child refuses immediately while an index fetch is pending", async (t) => {
  const launch = boot(t, { exitAtFetch: true });
  await assert.rejects(fastFailure(launch.promise), (error) => {
    assert.match(error.message, /exited.*exit code 23/);
    assert.match(error.message, /fixture exit detail token=<redacted>/);
    assert.ok(!error.message.includes("fixture-secret"));
    return true;
  });
  assert.equal(launch.counts().attempts, 1);
  assert.equal(launch.counts().wsProbes, 0);
  assert.equal(launch.children[0].listenerCount("exit"), 0);
  assert.equal(launch.signals[0].aborted, true, "the outstanding fetch also gets cancellation");
});

test("an exit during WS proof cannot publish ready coordinates", async (t) => {
  const launch = boot(t, { exitAtWs: true });
  await assert.rejects(fastFailure(launch.promise), /exited.*exit code 23/);
  assert.equal(launch.counts().wsProbes, 1);
});

test("the outer timer refuses a fetch that never returns", async (t) => {
  const launch = boot(t, { readyTimeoutMs: 5, hangFetch: true });
  await assert.rejects(fastFailure(launch.promise), /did not become ready within 5ms/);
  assert.equal(launch.counts().attempts, 1);
});

test("an unverifiable child uses the bounded fallback", async (t) => {
  await assert.rejects(boot(t, { unknownLifetime: true }).promise, /did not become ready within 60000ms/);
});
