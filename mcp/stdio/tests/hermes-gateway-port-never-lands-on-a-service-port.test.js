// The per-agent hermes gateway port is a hash into 8642–9641, and that range contains the ports the
// other tiers listen on: the service (8800), Dashboard Next (8801) and aify-env (8802). On 2026-09-08
// "general-helper-gpt" hashed to 8800; its launcher's kill_prior then killed every process with a
// socket on the service port, taking aify-env and every bridge down with it. The launcher fix scopes
// that kill; this fix stops the collision at the source.
import { test } from "node:test";
import assert from "node:assert/strict";
import { agentPort, PORT_BASE, PORT_SPAN, RESERVED_PORTS } from "../hermes-endpoint.js";

test("the agent that hashed onto the service port no longer gets it", () => {
  const port = agentPort("general-helper-gpt");
  assert.notEqual(port, 8800);
  assert.equal(RESERVED_PORTS.has(port), false);
});

test("no agent id can land on a reserved port, and every other port stays reachable", () => {
  const seen = new Set();
  for (let n = 0; seen.size < PORT_SPAN - RESERVED_PORTS.size && n < 200000; n += 1) {
    const port = agentPort(`agent-${n}`);
    assert.equal(RESERVED_PORTS.has(port), false, `agent-${n} -> ${port}`);
    assert.ok(port >= PORT_BASE && port < PORT_BASE + PORT_SPAN, `agent-${n} -> ${port} is inside the range`);
    seen.add(port);
  }
  assert.equal(seen.size, PORT_SPAN - RESERVED_PORTS.size, "every non-reserved port is still assigned to someone");
});
