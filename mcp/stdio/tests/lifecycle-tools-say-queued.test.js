// D9c: a DEFINED agent's restart or removal is queued for its host. The tool says queued and names the
// request; it never says "restarted" or "Removed", and a queued removal keeps the agent's local tracking,
// since the agent exists until its host has removed it. In-memory fetch only, never a listener.

import assert from "node:assert/strict";
import test from "node:test";

process.env.AIFY_SERVER_URL = "http://d9c-fixture.invalid";
process.env.CLAUDE_MCP_SERVER_URL = "http://d9c-fixture.invalid";
process.env.AIFY_API_KEY = "synthetic-test-key";
process.env.CLAUDE_MCP_API_KEY = "synthetic-test-key";

let reply;
const savedFetch = globalThis.fetch;
globalThis.fetch = async (url, options) => {
  assert.equal(new URL(url).origin, "http://d9c-fixture.invalid");
  const path = new URL(url).pathname.replace(/^\/api\/v1/, "");
  return new Response(JSON.stringify(reply(path, options.method, options.body ? JSON.parse(options.body) : null)), { status: 200 });
};
test.after(() => { globalThis.fetch = savedFetch; });

const { registerLifecycleTools } = await import("../lifecycle-tools.mjs");
const state = await import("../bridge-agent-state.mjs");
const { z } = await import("zod");
const tools = new Map();
registerLifecycleTools({ tool: (name, _d, _s, handler) => tools.set(name, handler) }, z);
const text = (result) => result.content.map((row) => row.text).join("\n");
const queued = (action) => ({ ok: true, queued: true, action, request: { id: `legacy-${action}`, status: "pending" } });

test("a queued removal names its request and keeps the agent tracked", async () => {
  state.REMOTE_AGENT_STATE.set("defined-agent", { marker: "kept" });
  reply = () => queued("delete");
  const said = text(await tools.get("comms_remove_agent")({ agentId: "defined-agent" }));
  assert.match(said, /queued as lifecycle request legacy-delete \[pending\]/);
  assert.doesNotMatch(said, /Removed agent/);
  assert.deepEqual(state.REMOTE_AGENT_STATE.get("defined-agent"), { marker: "kept" });
});

test("control: an undefined agent's removal is still done at once and forgotten", async () => {
  state.REMOTE_AGENT_STATE.set("plain", { marker: "gone" });
  reply = () => ({ ok: true, agentId: "plain" });
  assert.match(text(await tools.get("comms_remove_agent")({ agentId: "plain" })), /Removed agent "plain"/);
  assert.equal(state.REMOTE_AGENT_STATE.has("plain"), false);
});

test("restart and reset target the agent without asking for session history", async () => {
  for (const freshContext of [false, true]) {
    const calls = [];
    reply = (path, method, body) => {
      calls.push([method, path]);
      if (method === "POST") assert.deepEqual(body, { action: freshContext ? "recreate" : "restart", from_agent: "agent" });
      if (method === "GET" && path === "/agents/defined-agent") return { agent: { sessionMode: "managed" } };
      assert.equal(path, "/agents/defined-agent/control", "restart must not infer a target from session history");
      return queued("restart");
    };
    const said = text(await tools.get("comms_restart")({ agentId: "defined-agent", freshContext }));
    assert.match(said, /queued as lifecycle request legacy-restart \[pending\]/);
    assert.match(said, /GET \/api\/v1\/agent-lifecycle-requests\/legacy-restart/);
    assert.doesNotMatch(said, /restarted|reset with a fresh context|worker is stopped/);
    assert.deepEqual(calls, [["GET", "/agents/defined-agent"], ["POST", "/agents/defined-agent/control"]]);
  }
});

test("a nonqueued or malformed restart receipt never claims a completed effect", async () => {
  for (const answer of [{ ok: true }, {}, { queued: true, request: {} }, { queued: true, request: { id: "" } }]) {
    reply = (path, method) => method === "GET" && path === "/agents/defined-agent"
      ? { agent: { sessionMode: "managed" } } : path === "/sessions"
        ? { sessions: [{ id: "old", status: "stopped" }] } : answer;
    const result = await tools.get("comms_restart")({ agentId: "defined-agent" });
    assert.equal(result.isError, true, "a missing queued receipt must fail closed");
    assert.match(text(result), /no lifecycle request receipt/);
    assert.doesNotMatch(text(result), /restarted|worker is stopped/);
  }
});
