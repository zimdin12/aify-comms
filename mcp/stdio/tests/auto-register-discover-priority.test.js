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
import { ClaudeAdapter } from "../adapters/claude.js";

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

// A `claude` an agent runs from its own shell (sc-manager, 2026-09-26). It inherits the agent's session, and
// the capture store keyed by the agent's id still names the parent's session, so discovery answers the
// PARENT's handle; a registration with it took the live agent over. Claude Code sets CLAUDE_PID in its
// shells and CLAUDE_CODE_SESSION_ID in its MCP servers, so only such a bridge sees both.
const PARENT = "11111111-parent-session";
const NESTED = "22222222-nested-session";
function claudeDiscovering(handle) {
  const adapter = new ClaudeAdapter();
  adapter.discoverSessionId = async () => handle;
  return adapter;
}

test("a claude started from a Claude Code shell registers the session Claude Code gave its bridge", async () => {
  const result = await computeInitialSessionHandle({
    adapter: claudeDiscovering(PARENT),
    envHandle: PARENT,
    env: { CLAUDE_PID: "4242", CLAUDE_CODE_SESSION_ID: NESTED, CLAUDE_SESSION_ID: PARENT },
  });
  assert.equal(result, NESTED);
});

test("CONTROL: any other claude keeps discovery's answer, even where Claude Code names another session", async () => {
  // A fresh launch whose capture store still names the previous session: the heartbeat corrects that
  // handle, and the same-session takeover it allows is how a quick relaunch binds. Unchanged here.
  const result = await computeInitialSessionHandle({
    adapter: claudeDiscovering(PARENT),
    envHandle: "",
    env: { CLAUDE_CODE_SESSION_ID: "33333333-fresh-launch" },
  });
  assert.equal(result, PARENT);
});

test("CONTROL: CLAUDE_PID without a session from Claude Code falls back to discovery", async () => {
  const result = await computeInitialSessionHandle({
    adapter: claudeDiscovering(PARENT),
    envHandle: "",
    env: { CLAUDE_PID: "4242" },
  });
  assert.equal(result, PARENT);
});
