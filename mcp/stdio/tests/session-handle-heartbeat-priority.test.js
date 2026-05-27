// Plan 6 A1 (2026-05-26): the heartbeat must prefer discoverSessionId()
// (runtime-authoritative) over getCurrentSessionId() (env-read). Operators
// routinely leave stale HERMES_SESSION_ID / CODEX_THREAD_ID in their shells,
// and the prior fallback order (env first, discover only when env was null)
// pinned those stale values in the server's stored handle indefinitely.

import { test } from "node:test";
import assert from "node:assert/strict";
import { startSessionHandleHeartbeat } from "../session-handle-heartbeat.js";

test("heartbeat prefers discoverSessionId over getCurrentSessionId when both return values", async () => {
  const calls = [];
  const adapter = {
    getCurrentSessionId() { calls.push("env"); return "stale-env-id"; },
    async discoverSessionId() { calls.push("discover"); return "fresh-discover-id"; },
  };
  const posted = [];
  const stop = startSessionHandleHeartbeat({
    agentId: "test-agent",
    adapter,
    postFn: async (agentId, handle) => { posted.push({ agentId, handle }); },
    intervalMs: 50,
  });
  await new Promise((r) => setTimeout(r, 80));
  stop();
  assert.deepEqual(
    posted.map((p) => p.handle),
    ["fresh-discover-id"],
    "Plan 6 A1: discover result must win over env",
  );
});

test("heartbeat falls back to env when discoverSessionId returns null", async () => {
  const adapter = {
    getCurrentSessionId() { return "env-only-id"; },
    async discoverSessionId() { return null; },
  };
  const posted = [];
  const stop = startSessionHandleHeartbeat({
    agentId: "test-agent",
    adapter,
    postFn: async (agentId, handle) => { posted.push({ agentId, handle }); },
    intervalMs: 50,
  });
  await new Promise((r) => setTimeout(r, 80));
  stop();
  assert.deepEqual(posted.map((p) => p.handle), ["env-only-id"]);
});

test("heartbeat falls back to env when discoverSessionId throws", async () => {
  const adapter = {
    getCurrentSessionId() { return "env-only-id"; },
    async discoverSessionId() { throw new Error("gateway unreachable"); },
  };
  const posted = [];
  const stop = startSessionHandleHeartbeat({
    agentId: "test-agent",
    adapter,
    postFn: async (agentId, handle) => { posted.push({ agentId, handle }); },
    intervalMs: 50,
  });
  await new Promise((r) => setTimeout(r, 80));
  stop();
  assert.deepEqual(posted.map((p) => p.handle), ["env-only-id"]);
});
