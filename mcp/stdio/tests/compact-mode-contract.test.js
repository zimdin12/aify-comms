#!/usr/bin/env node
// `comms_compact`'s two modes, and which of them actually works.
//
// Compaction replaces an agent's live working memory with a summary of it. `handoff` builds a portable
// packet and spawns a fresh managed backing from it — reliable, and the default. `internal` asks the runtime
// to compact in place and currently refuses unless an adapter proves native support.
//
// THIS FILE WAS A SOURCE REGEX over `server.js` and is now behavioural. It matched strings like
// `const selectedMode = mode || "handoff"` as literal text, which meant it also asserted the file's
// indentation and its path — so it broke when the tool moved to `compact-tool.mjs` in v0.5.4 with no
// behavioural change, the same way `comms-contracts-defaults.test.js` did one slice earlier.
//
// A source pin can only prove a line was WRITTEN. These assertions register the real tool on a fake MCP
// server, call the real handler, and read what it put on the wire off a real loopback service — which proves
// the default REACHED THE SERVICE. That is strictly more than the regex proved, and it is the reason the
// upgrade is not merely a repair.

import assert from "node:assert/strict";
import http from "node:http";

const requests = [];
let sessionsPayload = { sessions: [] };
let messagesPayload = { messages: [] };

const server = http.createServer((req, res) => {
  let body = "";
  req.on("data", (chunk) => { body += chunk; });
  req.on("end", () => {
    const url = req.url.split("?")[0];
    requests.push({ method: req.method, url, body: body ? JSON.parse(body) : null });
    res.writeHead(200, { "content-type": "application/json" });
    // `/agents` FIRST. The handler resolves the target before anything else, and my first version of this
    // fake did not serve it — so the target was never found, no spawn happened, and the assertion failed for
    // a reason that had nothing to do with mode defaults. Read the handler's call sequence rather than
    // guessing which endpoints matter.
    if (url.endsWith("/agents")) {
      return res.end(JSON.stringify({ agents: { target: { role: "coder", runtime: "claude-code", sessionMode: "managed" } } }));
    }
    if (url.endsWith("/sessions")) return res.end(JSON.stringify(sessionsPayload));
    if (url.includes("/messages")) return res.end(JSON.stringify(messagesPayload));
    return res.end(JSON.stringify({ ok: true, agentId: "target", spawned: true }));
  });
});
await new Promise((resolve) => server.listen(0, "127.0.0.2", resolve));
server.unref();

process.env.AIFY_SERVER_URL = `http://127.0.0.2:${server.address().port}`;
process.env.CLAUDE_MCP_SERVER_URL = "";

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
// `compactMode` lives under `metadata`, not at the top level. I selected on `r.body.compactMode` first and
// found nothing while the handler had in fact succeeded — the fifth time in this decomposition that I wrote a
// test against an assumed payload shape instead of reading the call. `agentId` IS top-level.
const lastSpawn = () => requests.filter((r) => r.method === "POST" && r.body?.metadata?.compactMode).at(-1);

// ── The schema still offers exactly the two modes ────────────────────────────
const modes = compact.schema.mode.unwrap().options;
assert.deepEqual([...modes].sort(), ["handoff", "internal"], "compact must expose handoff and internal");

// ── The description leads with the destruction, because nothing else warns ───
// Whatever the target knew and never wrote down is gone, and nothing recovers it. An agent that reads this
// as routine hygiene has already lost the context by the time it finds out.
assert.match(compact.description, /DESTRUCTIVE TO CONTEXT/,
  "the description must lead with what compaction destroys");
assert.match(compact.description, /record open decisions somewhere durable FIRST/i,
  "…and say what to do before calling it");

// ── handoff is the DEFAULT, proven by what reaches the service ───────────────
sessionsPayload = { sessions: [{ id: "s1", agentId: "target", runtime: "claude-code", sessionMode: "managed", status: "running" }] };
messagesPayload = { messages: [{ id: "m1", from: "a", to: "target", subject: "s", body: "b", timestamp: "2026-08-13T00:00:00Z" }] };

{
  const res = await compact.handler({ targetAgentId: "target", from: "agent-a" });
  assert.ok(!res.isError, `default-mode compact failed: ${text(res)}`);
  const spawn = lastSpawn();
  assert.ok(spawn, "a handoff must reach the service as a spawn carrying compactMode");
  assert.equal(spawn.body.metadata.compactMode, "handoff", "omitting mode must default to handoff on the WIRE");
}

// ── the successor defaults to the SAME agent id ──────────────────────────────
// Compaction is meant to be invisible to everyone addressing that agent. A successor under a new id would
// silently orphan every sender still writing to the old one.
assert.equal(lastSpawn().body.agentId, "target", "the successor must keep the same agent ID by default");

{
  const res = await compact.handler({ targetAgentId: "target", from: "agent-a", newAgentId: "target-v2" });
  assert.ok(!res.isError, `explicit successor failed: ${text(res)}`);
  assert.equal(lastSpawn().body.agentId, "target-v2", "an explicit newAgentId must be honoured");
}

// ── internal is a real branch that REFUSES, and names what it could not do ───
// Not dead code: adapters are expected to grow native compaction. The refusal must identify the session and
// runtime, or an operator cannot tell which target lacks support.
{
  const res = await compact.handler({ targetAgentId: "target", from: "agent-a", mode: "internal" });
  assert.equal(res.isError, true, "internal compaction must refuse until an adapter proves support");
  assert.match(text(res), /not supported/i);
  assert.match(text(res), /claude-code/, "the refusal must name the runtime that lacks support");
  assert.match(text(res), /handoff/, "…and point at the mode that does work");
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
