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
  return new Response(JSON.stringify(reply(path, options.method)), { status: 200 });
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

test("a queued restart names its request and never says restarted", async () => {
  reply = (path, method) => {
    if (method === "GET" && path === "/agents/defined-agent") return { agent: { sessionMode: "managed" }, sessionMode: "managed" };
    if (method === "GET" && path === "/sessions") return { sessions: [{ id: "sess-1", agentId: "defined-agent", status: "running" }] };
    return queued("restart");
  };
  const said = text(await tools.get("comms_restart")({ agentId: "defined-agent" }));
  assert.match(said, /queued as lifecycle request legacy-restart \[pending\]/);
  assert.doesNotMatch(said, /restarted|reset with a fresh context/);
});
