#!/usr/bin/env node
// `comms_compact`'s two modes, proven by what reaches a real loopback service.
//
// native  types the runtime's own command through `POST /agents/{id}/compact/native`; the service decides
//         whether it may (`service/api_core/compaction.py`, tested in Python), so what this file proves is
//         that the tool ASKS it, reports its refusal verbatim, and never falls back to a handoff.
// handoff spawns a fresh session whose first message is the service's brief
//         (`GET /agents/{id}/compact/handoff-brief`) -- no copied message bodies.
//
// A source pin can only prove a line was WRITTEN. These register the real tool on a fake MCP server, call
// the real handler, and read what it put on the wire.

import assert from "node:assert/strict";
import http from "node:http";

const requests = [];
let sessionsPayload = { sessions: [] };
let nativeReply = { ok: true, command: "/compact", terminalId: "term_1", controlId: "ctl_1" };

const server = http.createServer((req, res) => {
  let body = "";
  req.on("data", (chunk) => { body += chunk; });
  req.on("end", () => {
    const [url, query = ""] = req.url.split("?");
    requests.push({ method: req.method, url, query, body: body ? JSON.parse(body) : null });
    res.writeHead(200, { "content-type": "application/json" });
    if (url.endsWith("/agents")) {
      return res.end(JSON.stringify({ agents: { target: { role: "coder", runtime: "claude-code", sessionMode: "managed" } } }));
    }
    if (url.endsWith("/sessions")) return res.end(JSON.stringify(sessionsPayload));
    if (url.endsWith("/compact/handoff-brief")) {
      const n = new URLSearchParams(query).get("recentMessages") ?? "10";
      return res.end(JSON.stringify({ ok: true, recentMessages: Number(n), text: `BRIEF read ${n}` }));
    }
    if (url.endsWith("/compact/native")) return res.end(JSON.stringify(nativeReply));
    if (url.includes("/messages")) return res.end(JSON.stringify({ messages: [{ from: "a", to: "target", body: "OLD BODY" }] }));
    return res.end(JSON.stringify({ ok: true, spawnRequest: { id: "sr_1", status: "queued" } }));
  });
});
await new Promise((resolve) => server.listen(0, "127.0.0.2", resolve));
server.unref();

process.env.AIFY_SERVER_URL = `http://127.0.0.2:${server.address().port}`;
process.env.CLAUDE_MCP_SERVER_URL = "";
delete process.env.AIFY_AGENT_ID;
delete process.env.AIFY_COMMS_AGENT_ID;

const { registerCompactTool } = await import("../compact-tool.mjs");
const { z } = await import("zod");

const tools = new Map();
registerCompactTool(
  { tool: (name, description, schema, handler) => tools.set(name, { name, description, schema, handler }) },
  z,
);
const compact = tools.get("comms_compact");
assert.ok(compact, "comms_compact must be registered");
const text = (res) => res.content[0].text;
const lastSpawn = () => requests.filter((r) => r.method === "POST" && r.url.endsWith("/spawn-requests")).at(-1);
const posts = (suffix) => requests.filter((r) => r.method === "POST" && r.url.endsWith(suffix));

// ── The schema offers exactly the two modes ──────────────────────────────────
assert.deepEqual([...compact.schema.mode.unwrap().options].sort(), ["handoff", "native"]);

// ── The description leads with the destruction ───────────────────────────────
assert.match(compact.description, /DESTRUCTIVE TO CONTEXT/, "the description must lead with what compaction destroys");
assert.match(compact.description, /record open decisions somewhere durable FIRST/i, "…and say what to do before calling it");

sessionsPayload = { sessions: [{ id: "s1", agentId: "target", runtime: "claude-code", environmentId: "env-1", status: "running" }] };

// ── handoff is the DEFAULT, and its first message is the service's brief ─────
{
  const res = await compact.handler({ targetAgentId: "target", from: "agent-a" });
  assert.ok(!res.isError, `default-mode compact failed: ${text(res)}`);
  const spawn = lastSpawn();
  assert.equal(spawn.body.metadata.compactMode, "handoff", "omitting mode must default to handoff on the WIRE");
  assert.equal(spawn.body.metadata.compactedFromAgentId, "target", "start_intent reads this to replace the live worker");
  assert.equal(spawn.body.agentId, "target", "the successor keeps the same agent ID by default");
  assert.equal(spawn.body.initialMessage, "BRIEF read 10", "the brief is the first message, and the service's default count stands");
  assert.equal(spawn.body.metadata.recentMessagesToRead, 10);
  const briefAsk = requests.filter((r) => r.url.endsWith("/compact/handoff-brief")).at(-1);
  assert.equal(new URLSearchParams(briefAsk.query).get("sessionId"), "s1", "the brief is about the session being replaced");
  assert.ok(!requests.some((r) => r.url.includes("/messages")), "no message bodies are fetched for a handoff any more");
  assert.equal(posts("/compact/native").length, 0, "a handoff never types into the console");
}

// ── the caller's count and instructions reach the fresh session ──────────────
{
  const res = await compact.handler({ targetAgentId: "target", from: "agent-a", recentMessages: 3, instructions: "finish the parser" });
  assert.ok(!res.isError, text(res));
  assert.equal(lastSpawn().body.initialMessage, "BRIEF read 3\n\nInstructions:\nfinish the parser");
  assert.match(text(res), /read its last 3 message/);
}

{
  const res = await compact.handler({ targetAgentId: "target", from: "agent-a", newAgentId: "target-v2" });
  assert.ok(!res.isError, text(res));
  assert.equal(lastSpawn().body.agentId, "target-v2", "an explicit newAgentId must be honoured");
}

// ── native asks the service, and types nothing itself ────────────────────────
{
  const spawnsBefore = posts("/spawn-requests").length;
  const res = await compact.handler({ targetAgentId: "target", from: "agent-a", mode: "native" });
  assert.ok(!res.isError, text(res));
  const ask = posts("/compact/native").at(-1);
  assert.equal(ask.url, "/api/v1/agents/target/compact/native");
  assert.deepEqual(ask.body, { from: "agent-a" }, "the command is the service's to choose; the tool sends only who asks");
  assert.match(text(res), /QUEUED \/compact/);
  assert.match(text(res), /Not confirmation/, "a queued keystroke must not be reported as a compaction that happened");
  assert.equal(posts("/spawn-requests").length, spawnsBefore, "native must not also spawn a handoff");
}

// ── a refusal is reported verbatim and does NOT fall back to a handoff ───────
{
  nativeReply = { ok: false, refused: "mid-turn", message: "target is mid-turn: /compact typed now would be queued" };
  const spawnsBefore = posts("/spawn-requests").length;
  const res = await compact.handler({ targetAgentId: "target", from: "agent-a", mode: "native" });
  assert.equal(res.isError, true);
  assert.equal(text(res), nativeReply.message);
  assert.equal(posts("/spawn-requests").length, spawnsBefore, "a refused native compaction must not become a handoff");
}

// ── handoff-only fields are refused with native, before anything is sent ─────
{
  const before = requests.length;
  const res = await compact.handler({ targetAgentId: "target", from: "agent-a", mode: "native", recentMessages: 5, newAgentId: "x" });
  assert.equal(res.isError, true);
  assert.match(text(res), /newAgentId, recentMessages apply to mode "handoff" only/);
  assert.equal(requests.length, before, "nothing may reach the service");
}

// ── no eligible session is reported, not silently treated as success ─────────
{
  sessionsPayload = { sessions: [] };
  const res = await compact.handler({ targetAgentId: "target", from: "agent-a" });
  assert.equal(res.isError, true, "with no compactable session this must be an error");
  assert.ok(!/undefined|\[object Object\]/.test(text(res)), `leaked a placeholder: ${text(res)}`);
}

server.close();
console.log("compact-mode-contract.test.js: all assertions passed");
