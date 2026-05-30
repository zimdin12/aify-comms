#!/usr/bin/env node
// Unit tests for the hermes RuntimeAdapter (api_server delivery model).
//
// Task C2 (2026-05-30 hermes-apiserver-delivery plan): discoverSessionId must
// return the STABLE per-agent pinned api_server session id (pinnedSessionId),
// keyed by the adapter's agent-id resolution — NEVER the gateway global
// session.most_recent. Capabilities advertise the channel/api_server delivery
// model with the retired tui_gateway WS bind path removed.

import assert from "node:assert/strict";
import { test } from "node:test";
import { HermesAdapter } from "../adapters/hermes.js";
import { pinnedSessionId } from "../hermes-session-id.js";

test("discoverSessionId returns the pinned per-agent session id from opts.agentId", async () => {
  const adapter = new HermesAdapter();
  const id = await adapter.discoverSessionId({ agentId: "sc-coder", env: {} });
  assert.equal(id, pinnedSessionId("sc-coder"));
});

test("discoverSessionId resolves agentId from AIFY_AGENT_ID env when not passed", async () => {
  const adapter = new HermesAdapter();
  const id = await adapter.discoverSessionId({ env: { AIFY_AGENT_ID: "sc-tester" } });
  assert.equal(id, pinnedSessionId("sc-tester"));
});

test("discoverSessionId prefers explicit agentId over env", async () => {
  const adapter = new HermesAdapter();
  const id = await adapter.discoverSessionId({
    agentId: "explicit-agent",
    env: { AIFY_AGENT_ID: "env-agent" },
  });
  assert.equal(id, pinnedSessionId("explicit-agent"));
});

test("discoverSessionId never returns a gateway-global most_recent value", async () => {
  const adapter = new HermesAdapter();
  // Even with a gateway URL set in the (legacy) env, the pinned id must be a
  // pure function of the agentId — never a value pulled off the gateway.
  const id = await adapter.discoverSessionId({
    agentId: "sc-coder",
    env: { AIFY_HERMES_GATEWAY_URL: "ws://127.0.0.1:9999" },
  });
  assert.equal(id, pinnedSessionId("sc-coder"));
  assert.match(id, /^aify-/);
});

test("discoverSessionId returns null when no agentId is resolvable", async () => {
  const adapter = new HermesAdapter();
  const id = await adapter.discoverSessionId({ env: {} });
  assert.equal(id, null);
});

test("capabilities advertise the channel/api_server model (steer off, interrupt on)", () => {
  const adapter = new HermesAdapter();
  // api_server chat has no mid-turn steer; /v1/runs/{id}/stop gives interrupt.
  assert.equal(adapter.supportsSteering, false);
  assert.equal(adapter.supportsInterrupt, true);
  assert.equal(adapter.supportsManaged, true);
});
