// Plan 6 A2 (2026-05-26): the auto-register path must apply the same
// discover-first / env-fallback priority as the heartbeat (A1). Without
// this, the FIRST registration of an agent (before the 60s heartbeat
// fires) still gets the stale env value baked into agent.session_handle
// and runtime_state.sessionId. Subsequent dispatches in that 60s window
// fail at prompt.submit time.

import { test } from "node:test";
import assert from "node:assert/strict";
// Imported from its OWNER, not from `server.js`. It used to come from the bin entry point, which meant
// testing nine lines of session-handle precedence loaded the entire bridge.
import { computeInitialSessionHandle } from "../auto-registration.mjs";

test("computeInitialSessionHandle prefers discoverSessionId over env-default", async () => {
  const adapter = {
    getCurrentSessionId() { return "stale-env-id"; },
    async discoverSessionId() { return "fresh-discover-id"; },
  };
  const result = await computeInitialSessionHandle({ adapter, envHandle: "stale-env-id" });
  assert.equal(result, "fresh-discover-id");
});

test("computeInitialSessionHandle falls back to env when discover returns null", async () => {
  const adapter = {
    getCurrentSessionId() { return "env-fallback-id"; },
    async discoverSessionId() { return null; },
  };
  const result = await computeInitialSessionHandle({ adapter, envHandle: "env-fallback-id" });
  assert.equal(result, "env-fallback-id");
});

test("computeInitialSessionHandle falls back to env when discover throws", async () => {
  const adapter = {
    getCurrentSessionId() { return "env-fallback-id"; },
    async discoverSessionId() { throw new Error("gateway down"); },
  };
  const result = await computeInitialSessionHandle({ adapter, envHandle: "env-fallback-id" });
  assert.equal(result, "env-fallback-id");
});

test("computeInitialSessionHandle returns empty string when both unavailable", async () => {
  const adapter = {
    getCurrentSessionId() { return null; },
    async discoverSessionId() { return null; },
  };
  const result = await computeInitialSessionHandle({ adapter, envHandle: "" });
  assert.equal(result, "");
});

test("computeInitialSessionHandle handles missing adapter gracefully", async () => {
  const result = await computeInitialSessionHandle({ adapter: null, envHandle: "env-id" });
  assert.equal(result, "env-id");
});

test("computeInitialSessionHandle trims whitespace from discover result", async () => {
  const adapter = {
    async discoverSessionId() { return "  fresh-id  "; },
  };
  const result = await computeInitialSessionHandle({ adapter, envHandle: "" });
  assert.equal(result, "fresh-id");
});
