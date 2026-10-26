#!/usr/bin/env node
// How long a dead hermes gateway goes unnoticed.
//
// `DEFAULT_GATEWAY_PROBE_INTERVAL_MS` was named by no test. (`isPortFree` was tested here too until the gateway
// port left comms in 0.9.10.)
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

import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_GATEWAY_PROBE_INTERVAL_MS,
  DEFAULT_GATEWAY_PROBE_THRESHOLD,
  startGatewayLivenessProbe,
} from "../hermes-gateway-liveness.js";

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
