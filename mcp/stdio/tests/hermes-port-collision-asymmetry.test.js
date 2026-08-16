#!/usr/bin/env node
// Two agents can hash to one port. ONE of the two things that binds a port resolves that; the other
// does not.
//
// `agentPort(agentId)` is `PORT_BASE + fnv1a(id) % PORT_SPAN` over the documented 8642–9641 range —
// 1000 slots. On the birthday bound two agents in a 20-agent fleet collide with roughly 17%
// probability, and at 37 agents it is even money. This is not an edge case at fleet scale.
//
// THE WS GATEWAY HANDLES IT. `resolveGatewayPort` reuses a persisted port when no other agent has
// claimed it, otherwise probes forward for one that is both bindable and unclaimed, then persists
// the choice. Its own comment names the failure it exists for: "the idempotent reuse-probe could
// attach to the OTHER agent's gateway".
//
// THE api_server DAEMON DOES NOT. `agentEndpoint` returns the raw hash port and never consults a
// persisted port or another agent's claim. `hermes-daemon-cli.js` takes only `<agentId>`, so
// `ensureDaemon` derives through `agentEndpoint`, and `hermes-channel.js::resolveHermesEndpoint`
// connects through the same raw hash when no explicit override is in the environment.
//
// A RETRACTION, recorded rather than quietly dropped. I previously suspected the daemon LAUNCH and
// the daemon CONNECT disagreed — one resolving a shifted port, the other recomputing the hash — and
// declined to claim it as unproven. Traced: they do not disagree. Both go through `agentEndpoint`,
// and the gateway path goes through `resolveGatewayPort` at both ends, so each subsystem is
// internally consistent. What is actually true is narrower and worth pinning instead: the two
// subsystems answer the collision question differently, and only one of them answers it.
//
// This asserts the CONTRAST, using the injectable `portFree` seam so no real socket is bound.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { agentEndpoint, agentPort, resolveGatewayPort } from "../hermes-endpoint.js";

const PORT_BASE = 8642;
const PORT_SPAN = 1000;

function tempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "aify-portsym-"));
}

// ── the hash lives in the documented range and is deterministic ──────────────────────────────
{
  for (const id of ["lc-coder", "team.coder", "a", "z".repeat(60)]) {
    const port = agentPort(id);
    assert.ok(
      Number.isInteger(port) && port >= PORT_BASE && port < PORT_BASE + PORT_SPAN,
      `agentPort(${id}) = ${port}, outside the documented 8642-9641 range`,
    );
    assert.equal(agentPort(id), port, "the same id must always hash to the same port");
  }
}

// ── a collision is REACHABLE, demonstrated rather than argued from probability ────────────────
{
  // Search the id space for two distinct ids that hash to one port. If this ever fails to find a
  // pair, the hash or the span changed and the asymmetry below may no longer matter.
  const byPort = new Map();
  let pair = null;
  for (let i = 0; i < 4000 && !pair; i += 1) {
    const id = `agent-${i}`;
    const port = agentPort(id);
    if (byPort.has(port)) pair = [byPort.get(port), id, port];
    else byPort.set(port, id);
  }
  assert.ok(pair, "no two ids in 4000 collided; the port space is far larger than documented");
  const [a, b, port] = pair;
  assert.notEqual(a, b);
  assert.equal(agentPort(a), agentPort(b));
  assert.equal(agentPort(a), port);
}

// ── the GATEWAY shifts off a claimed port ────────────────────────────────────────────────────
{
  const dir = tempDir();
  try {
    const id = "lc-coder";
    const wanted = agentPort(id);
    // Every port reads as occupied except one well past the hash: the resolver must walk to it.
    const target = PORT_BASE + ((wanted - PORT_BASE + 5) % PORT_SPAN);
    const chosen = await resolveGatewayPort(id, {
      tempDir: dir,
      portFree: async (p) => p === target,
    });
    assert.equal(
      chosen, target,
      "resolveGatewayPort must probe forward to a bindable port rather than insisting on the hash",
    );
    assert.notEqual(chosen, wanted, "the whole point is that it does not return the raw hash here");

    // ...and persists it, so ensure-host, the delivery loop and the visible TUI agree.
    const persisted = fs.readFileSync(path.join(dir, `aify-hermes-port-${id}`), "utf8").trim();
    assert.equal(Number(persisted), target, "the resolved port must be persisted, not recomputed");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

// ── the DAEMON endpoint does not ─────────────────────────────────────────────────────────────
{
  const dir = tempDir();
  try {
    const id = "lc-coder";
    // Same agent, same conditions under which the gateway just moved. There is no seam to tell
    // agentEndpoint a port is taken, because it never asks.
    const ep = agentEndpoint(id, { tempDir: dir });
    assert.equal(
      ep.port, agentPort(id),
      "agentEndpoint returns the RAW hash. Pinned as the fact it is: two agents whose ids collide "
        + "both derive this port for their api_server daemon, and nothing here shifts either of "
        + "them. The WS gateway solved this for itself; this path did not.",
    );
    assert.equal(ep.baseUrl, `http://127.0.0.1:${ep.port}`);
    assert.ok(ep.key, "the per-agent key is what stops a colliding probe adopting another daemon");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

// ── the key is per-agent, which is what limits a collision to a bind failure ──────────────────
{
  const dir = tempDir();
  try {
    // Two agents, deliberately NOT dot-folding into one another (that collision is pinned
    // separately in agent-id-sanitiser-collision.test.js) — so the keys must differ.
    const a = agentEndpoint("lc-coder", { tempDir: dir });
    const b = agentEndpoint("lc-tester", { tempDir: dir });
    assert.notEqual(
      a.key, b.key,
      "colliding agents must at least fail to AUTHENTICATE against each other's daemon; a shared "
        + "key would turn a port collision into a silent cross-agent adoption",
    );
    assert.equal(agentEndpoint("lc-coder", { tempDir: dir }).key, a.key, "keys are stable per agent");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

console.log("hermes-port-collision-asymmetry.test.js: all assertions passed");
