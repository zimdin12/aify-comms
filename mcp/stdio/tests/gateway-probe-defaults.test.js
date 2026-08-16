#!/usr/bin/env node
// How long a dead hermes gateway goes unnoticed, and whether a port is actually free.
//
// `DEFAULT_GATEWAY_PROBE_INTERVAL_MS` and `isPortFree` were each named by no test.
//
// THE INTERVAL IS HALF OF A DURATION. On its own it says nothing; multiplied by the threshold it is
// the time a managed hermes agent can be dead while the dashboard still shows it alive — 3 probes at
// 30s, so 90 seconds. Both halves are pinned together with that product spelled out, because
// changing either silently changes the number an operator experiences and neither constant mentions
// the other.
//
// THE DEFAULTS ARE ALSO PROVED TO BE USED. A default that the driver ignores is a documented lie, so
// the driver is started WITHOUT timing arguments and its own log line is read back for the threshold
// it actually applied.
//
// `isPortFree` BINDS A REAL SOCKET, which is safe here in a way most socket tests are not: it binds
// an EPHEMERAL port on 127.0.0.1 that this test itself owns, never a fixed one, and never the
// service's. A test that probed a hardcoded port would be answering about whatever the operator
// happens to be running.

import assert from "node:assert/strict";
import net from "node:net";
import test from "node:test";

import {
  DEFAULT_GATEWAY_PROBE_INTERVAL_MS,
  DEFAULT_GATEWAY_PROBE_THRESHOLD,
  startGatewayLivenessProbe,
} from "../hermes-gateway-liveness.js";
import { isPortFree } from "../hermes-endpoint.js";

/** Bind an ephemeral port on 127.0.0.1 and hand back { port, close }. */
function listenEphemeral() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      resolve({
        port: srv.address().port,
        close: () => new Promise((r) => srv.close(r)),
      });
    });
  });
}

test("the shipped detection window is 3 probes at 30s — 90 seconds", () => {
  assert.equal(DEFAULT_GATEWAY_PROBE_INTERVAL_MS, 30_000);
  assert.equal(DEFAULT_GATEWAY_PROBE_THRESHOLD, 3);
  const windowMs = DEFAULT_GATEWAY_PROBE_INTERVAL_MS * DEFAULT_GATEWAY_PROBE_THRESHOLD;
  assert.equal(windowMs, 90_000,
    "this product is the time a dead gateway can still read as alive; change it on purpose");
  assert.ok(DEFAULT_GATEWAY_PROBE_THRESHOLD > 1,
    "one failed probe is a blip — latching dead on it would flap on every transient socket error");
});

test("the driver APPLIES the default threshold when none is passed", async () => {
  // A default nothing reads is a documented lie. Started with no timing arguments, the driver's own
  // log line has to name the threshold it used.
  const lines = [];
  let reported = null;
  const stop = startGatewayLivenessProbe({
    intervalMs: 5, // the interval is overridden so the test is fast; the THRESHOLD is the default
    probe: async () => ({ alive: false }),
    reportDead: async (info) => { reported = info; },
    log: (msg) => lines.push(msg),
  });
  await new Promise((r) => setTimeout(r, 120));
  stop();
  assert.ok(reported, "a gateway failing every probe was never reported dead");
  assert.equal(reported.consecutiveFailures, DEFAULT_GATEWAY_PROBE_THRESHOLD);
  assert.ok(lines.some((l) => l.includes(`>= ${DEFAULT_GATEWAY_PROBE_THRESHOLD}`)),
    "the log must name the threshold it applied, or an operator cannot tell why it fired");
});

test("a gateway that answers is never reported dead", async () => {
  let reported = false;
  const stop = startGatewayLivenessProbe({
    intervalMs: 5,
    probe: async () => ({ alive: true }),
    reportDead: async () => { reported = true; },
    log: () => {},
  });
  await new Promise((r) => setTimeout(r, 120));
  stop();
  assert.equal(reported, false);
});

test("an unusable interval disables the driver instead of spinning", async () => {
  // `intervalMs: 0` in a setInterval is a busy loop. Returning a no-op stop is the safe reading, and
  // it is what the caller gets for a missing probe or reporter too.
  for (const opts of [
    { intervalMs: 0 }, { intervalMs: -1 }, { intervalMs: NaN }, { probe: undefined }, {},
  ]) {
    // A FLAG, NOT A THROW. The driver swallows anything `reportDead` raises — deliberately, so a
    // reporting failure cannot break the latch — so a throwing spy proves nothing here. My first
    // version used one and the mutation admitting `intervalMs: 0` survived: the probe ran, the
    // report threw, the driver ate it, and the test still passed.
    let probed = false;
    let reported = false;
    const stop = startGatewayLivenessProbe({
      probe: async () => { probed = true; return { alive: false }; },
      reportDead: async () => { reported = true; },
      log: () => {},
      ...opts,
    });
    assert.equal(typeof stop, "function");
    // eslint-disable-next-line no-await-in-loop
    await new Promise((r) => setTimeout(r, 40));
    stop();
    assert.equal(probed, false, `${JSON.stringify(opts)} started probing anyway`);
    assert.equal(reported, false, `${JSON.stringify(opts)} reported a gateway dead`);
  }
});

// ── isPortFree ───────────────────────────────────────────────────────────────────────────────

test("a port nothing is listening on reads as free", async () => {
  // Take an ephemeral port, release it, then ask. Racy in theory, which is why the assertion is
  // about the answer being a boolean the caller can act on rather than about a fixed number.
  const held = await listenEphemeral();
  const port = held.port;
  await held.close();
  assert.equal(await isPortFree(port), true);
});

test("a port THIS TEST is listening on reads as taken", async () => {
  const held = await listenEphemeral();
  try {
    assert.equal(await isPortFree(held.port), false,
      "a bound port read as free — the gateway would be told to use a port already in use");
  } finally {
    await held.close();
  }
});

test("a port that cannot be bound at all resolves false rather than throwing", async () => {
  // Its callers use it to pick a port and do not wrap it; a throw would abort the launch instead of
  // moving to the next candidate.
  for (const port of [-1, 70000, 1.5, "nonsense"]) {
    assert.equal(await isPortFree(port), false, `port ${JSON.stringify(port)} did not resolve false`);
  }
});

test("it answers about the HOST it is given", async () => {
  // The default is 127.0.0.1. A port bound on loopback is free on another interface, and the gateway
  // binds loopback — so a check against the wrong host would hand out a port already in use.
  const held = await listenEphemeral();
  try {
    assert.equal(await isPortFree(held.port, "127.0.0.1"), false);
    assert.equal(await isPortFree(held.port, "127.0.0.2"), true,
      "the same port on a different loopback address is a different socket");
  } finally {
    await held.close();
  }
});
