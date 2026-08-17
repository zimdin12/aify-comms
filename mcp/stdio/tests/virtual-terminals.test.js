// Real tests for the virtual-terminal sink and registry, extracted from server.js in v0.5.4.
//
// The sink's retry loop exists because of an operator report (2026-05-22): pi terminal output stopped at
// "▶ turn started" with one character of the reply visible, because `text_delta` POSTs fell on the floor
// during a service-restart window. The fix retries transient failures three times with backoff — and
// treats 404 differently, because a missing terminal row will never come back and retrying it just delays
// invalidating a cache entry that is now wrong.
//
// None of that was testable while it lived in server.js, which no test imports at all.
//
// A REAL HTTP SERVER on 127.0.0.2, not a stubbed `httpCall`: the sink reaches the network through an
// imported binding that cannot be monkey-patched, and driving actual requests is what proves the retry
// and the 404 branch rather than a mock's idea of them.
//
// ONE SERVER FOR THE WHOLE FILE, with a handler the tests swap. My first version started a server per
// test and cache-busted the import of `virtual-terminals.mjs` — which does NOT bust
// `aify-service-endpoint.mjs` underneath it. That module resolves SERVER_URL at load and is cached by
// specifier, so every test after the first was posting at the first test's already-closed port: zero
// requests, three retries, 780ms of backoff, and a failure that looked like the sink was broken.

// ESM: mcp/stdio/package.json declares "type": "module", so a .js test here is a module and `require`
// is not defined in it.
import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

let HANDLER = (_n, res) => { res.writeHead(200); res.end("{}"); };
let COUNT = 0;
const REQUESTS = [];

const SERVER = http.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", () => {
    REQUESTS.push({ method: req.method, url: req.url, body });
    HANDLER(++COUNT, res);
  });
});

const PORT = await new Promise((resolve) => {
  SERVER.listen(0, "127.0.0.2", () => resolve(SERVER.address().port));
});

// Set BEFORE the import: aify-service-endpoint.mjs reads it at module load, once per process.
process.env.AIFY_SERVER_URL = `http://127.0.0.2:${PORT}`;
// The modules read `CLAUDE_MCP_SERVER_URL || AIFY_SERVER_URL` — the LEGACY name WINS, and a
// live wrapper environment exports it. Setting only the new name left the fake below unused.
process.env.CLAUDE_MCP_SERVER_URL = `http://127.0.0.2:${PORT}`;
process.env.AIFY_API_KEY = "test-key";
// Paired with the LEGACY name, which the modules read FIRST: a wrapper environment exports it,
// and leaving it set means the module sends the operator's real key instead of this one.
process.env.CLAUDE_MCP_API_KEY = "test-key";
const m = await import("../virtual-terminals.mjs");

function scenario(handler) {
  HANDLER = handler;
  COUNT = 0;
  REQUESTS.length = 0;
  m.VIRTUAL_TERMINALS_BY_AGENT.clear();
  return REQUESTS;
}

test.after(() => SERVER.close());

test("the sink refuses an empty terminal id", () => {
  scenario((_n, res) => { res.writeHead(200); res.end("{}"); });
  for (const bad of ["", "   ", null, undefined]) {
    assert.equal(m.createVirtualTerminalSink(bad), null, `"${bad}" must not produce a sink`);
  }
});

test("the sink sends nothing when there is neither output nor status", async () => {
  const requests = scenario((_n, res) => { res.writeHead(200); res.end("{}"); });
  await m.createVirtualTerminalSink("t1")("", "");
  assert.deepEqual(requests, [], "an empty frame must not cost a request");
});

test("a frame is POSTed to the terminal's output endpoint", async () => {
  const requests = scenario((_n, res) => { res.writeHead(200); res.end("{}"); });
  await m.createVirtualTerminalSink("term-1")("hello", "running");
  assert.equal(requests.length, 1);
  assert.equal(requests[0].method, "POST");
  assert.match(requests[0].url, /\/terminals\/term-1\/output$/);
  const sent = JSON.parse(requests[0].body);
  assert.equal(sent.output, "hello");
  assert.equal(sent.status, "running");
  assert.ok(sent.bridgeId, "the bridge id must travel with the frame");
});

test("a TRANSIENT failure is retried and the frame survives", async () => {
  // The operator-reported bug: without this, one restart blip loses the rest of a turn's output.
  const requests = scenario((n, res) => {
    if (n === 1) { res.writeHead(500); res.end("boom"); return; }
    res.writeHead(200); res.end("{}");
  });
  await m.createVirtualTerminalSink("term-2")("delta", "");
  assert.equal(requests.length, 2, "the first attempt failed and the second must have carried the frame");
  assert.equal(JSON.parse(requests[1].body).output, "delta");
});

test("a persistent failure gives up after THREE attempts without throwing", async () => {
  // Best-effort by design: a sink that throws would take down the turn it is reporting on.
  const requests = scenario((_n, res) => { res.writeHead(503); res.end("nope"); });
  const errors = [];
  const realError = console.error;
  console.error = (...args) => errors.push(args.join(" "));
  try {
    await m.createVirtualTerminalSink("term-3")("delta", "");
  } finally {
    console.error = realError;
  }
  assert.equal(requests.length, 3, "three attempts, then stop");
  assert.ok(errors.some((e) => e.includes("term-3")),
    "a dropped frame must be logged, or the loss is silent — which is what the report was about");
});

test("a 404 is NOT retried, and it invalidates the cached terminal", async () => {
  // A missing terminal row never comes back. Retrying it delays the cache invalidation that lets the next
  // ensure() create a fresh terminal, which is the actual recovery.
  const requests = scenario((_n, res) => { res.writeHead(404); res.end("gone"); });
  m.VIRTUAL_TERMINALS_BY_AGENT.set("coder", { terminalId: "term-4", runtime: "pi" });
  m.VIRTUAL_TERMINALS_BY_AGENT.set("tester", { terminalId: "other", runtime: "pi" });

  await m.createVirtualTerminalSink("term-4")("delta", "");
  assert.equal(requests.length, 1, "404 must not be retried");
  assert.equal(m.VIRTUAL_TERMINALS_BY_AGENT.has("coder"), false, "the dead terminal's entry must be dropped");
  assert.equal(m.VIRTUAL_TERMINALS_BY_AGENT.has("tester"), true,
    "…and only that one — another agent's terminal is unaffected");
});

test("ensureVirtualTerminal refuses a blank agent or runtime without calling the service", async () => {
  const requests = scenario((_n, res) => { res.writeHead(200); res.end("{}"); });
  for (const [agent, runtime] of [["", "pi"], ["coder", ""], ["  ", "  "], [null, null]]) {
    assert.equal(await m.ensureVirtualTerminal(agent, {}, runtime), null);
  }
  assert.deepEqual(requests, [], "a guard that still made the call would be no guard at all");
});

test("ensureVirtualTerminal reuses a cached terminal, but not across a runtime change", async () => {
  const requests = scenario((_n, res) => {
    res.writeHead(200); res.end(JSON.stringify({ terminal: { id: "fresh" } }));
  });
  m.VIRTUAL_TERMINALS_BY_AGENT.set("coder", { terminalId: "cached", runtime: "pi" });

  const same = await m.ensureVirtualTerminal("coder", {}, "pi");
  assert.equal(same.terminalId, "cached");
  assert.deepEqual(requests, [], "a cache hit must not cost a round trip on every dispatch");

  const changed = await m.ensureVirtualTerminal("coder", {}, "codex");
  assert.equal(changed.terminalId, "fresh",
    "a runtime change must re-ensure — reusing a pi terminal for codex would stream into the wrong console");
  assert.equal(requests.length, 1);
});

test("ensureVirtualTerminal throws when the service returns no terminal id", async () => {
  // Returning a half-built entry would cache a terminal id of "" and stream every frame into nothing.
  scenario((_n, res) => { res.writeHead(200); res.end(JSON.stringify({ terminal: {} })); });
  await assert.rejects(() => m.ensureVirtualTerminal("nobody", {}, "pi"), /no terminal id/);
});

// --- findAgentIdForVirtualTerminal ---------------------------------------------------------------
//
// Moved out of server.js in v0.5.4, to sit beside the map it searches. It answers who owns a virtual
// terminal, and the RUNTIME CHECK is the part with history: the original had a hardcoded
// `runtime === "pi"`, so when hermes/codex/opencode synth terminals were added the bridge routed their
// controls down the node-pty path and marked them stopped on every Console open (operator-reported
// 2026-05-22). The allowlist replaced that check; these assert it actually gates.
//
// Reached through the same cache-busted namespace as everything above — the module is imported once per
// process and its map is module state.

test("it finds the agent whose entry matches BOTH the terminal id and an RPC runtime", () => {
  m.VIRTUAL_TERMINALS_BY_AGENT.clear();
  m.VIRTUAL_TERMINALS_BY_AGENT.set("coder-1", { terminalId: "t1", runtime: "hermes" });
  assert.equal(m.findAgentIdForVirtualTerminal("t1"), "coder-1");
  m.VIRTUAL_TERMINALS_BY_AGENT.clear();
});

test("EVERY RPC RUNTIME RESOLVES — the 2026-05-22 regression was one hardcoded runtime", () => {
  // The bug this function's history is made of. A check admitting only `pi` sent hermes, codex and
  // opencode consoles down the PTY path, where they were marked stopped on every open.
  for (const runtime of m.VIRTUAL_RPC_RUNTIMES) {
    m.VIRTUAL_TERMINALS_BY_AGENT.clear();
    m.VIRTUAL_TERMINALS_BY_AGENT.set("a", { terminalId: "t1", runtime });
    assert.equal(m.findAgentIdForVirtualTerminal("t1"), "a", runtime);
  }
  m.VIRTUAL_TERMINALS_BY_AGENT.clear();
});

test("a NON-RPC runtime is not claimed, even with a matching terminal id", () => {
  // The complement: a real PTY terminal must NOT resolve here, or its input would be routed to the
  // virtual path and never reach the pty.
  m.VIRTUAL_TERMINALS_BY_AGENT.clear();
  m.VIRTUAL_TERMINALS_BY_AGENT.set("a", { terminalId: "t1", runtime: "claude" });
  assert.equal(m.findAgentIdForVirtualTerminal("t1"), "");
  m.VIRTUAL_TERMINALS_BY_AGENT.clear();
});

test("an unknown, blank or absent terminal id yields an empty string", () => {
  // `""` rather than null: callers compare it as a string, and the early return on a blank id is what
  // stops an empty attribute matching an entry that also has no terminal id.
  m.VIRTUAL_TERMINALS_BY_AGENT.clear();
  m.VIRTUAL_TERMINALS_BY_AGENT.set("a", { runtime: "pi" });
  assert.equal(m.findAgentIdForVirtualTerminal(""), "");
  assert.equal(m.findAgentIdForVirtualTerminal("   "), "");
  assert.equal(m.findAgentIdForVirtualTerminal(undefined), "");
  assert.equal(m.findAgentIdForVirtualTerminal("nope"), "");
  m.VIRTUAL_TERMINALS_BY_AGENT.clear();
});
