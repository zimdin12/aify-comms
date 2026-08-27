// Quota reporting, executed rather than scanned.
//
// THE PROPERTY WORTH TESTING IS UNKNOWN-VS-ZERO. Every number this prints can legitimately be absent: the
// collector warms up, a source goes stale, a token stops answering. A missing percentage rendered as "0%"
// would tell an operator a pool is exhausted when nothing at all is known about it — and the operator's
// response to those two states is opposite. Route away from an exhausted pool; investigate an unknown one.
//
// This is the same rule `aify-comms doctor` exists to enforce on the service side: no evidence is not a
// pass. Here it is also not a zero.

import assert from "node:assert/strict";
import test from "node:test";
import http from "node:http";
import { readFileSync } from "node:fs";
import path from "node:path";

import { STDIO_DIR, toolSources } from "./bridge-sources.mjs";

// A real loopback service, so the handler's own HTTP path runs. `routes` is swapped per test.
let routes = {};
const server = http.createServer((req, res) => {
  const key = req.url.split("?")[0];
  const body = Object.hasOwn(routes, key) ? routes[key] : null;
  res.writeHead(body === null ? 404 : 200, { "content-type": "application/json" });
  res.end(JSON.stringify(body === null ? { error: "not found" } : body));
});
await new Promise((resolve) => server.listen(0, "127.0.0.2", resolve));
// unref so the listening socket does not hold the event loop open — node --test waits for it to drain and
// the run hangs instead of finishing. The comms-contracts test closes its server at the end of a script;
// this file uses the test runner, which needs the handle to be non-blocking as well.
server.unref();

process.env.AIFY_SERVER_URL = `http://127.0.0.2:${server.address().port}`;
process.env.CLAUDE_MCP_SERVER_URL = "";
process.env.AIFY_AGENT_ID = "agent-a";

const usage = await import("../usage-tool.mjs");
const { z } = await import("zod");

const tools = new Map();
usage.registerUsageTool(
  { tool: (name, description, schema, handler) => tools.set(name, { name, description, schema, handler }) },
  z,
);
const tool = tools.get("comms_usage");
const text = (res) => res.content[0].text;

test("the tool registers and exports only its wrapper", () => {
  assert.ok(tool, "comms_usage must be registered");
  assert.deepEqual(Object.keys(usage).sort(), ["registerUsageTool"]);
});

test("the description says it is ADVISORY, not a gate", () => {
  // Nothing enforces quota from here. A description implying otherwise would have an agent stop working
  // because a pool looked low, when the intended response is to route elsewhere.
  assert.match(tool.description, /advisory/i);
  assert.match(tool.description, /hand work to a pool with headroom/i, "…and say what to do instead");
});

test("percentages are reported per pool, weekly and 5-hour", async () => {
  routes = {
    "/api/v1/usage": {
      pools: [{ source_id: "anthropic", weekly: { left_pct: 62 }, five_hour: { left_pct: 91 } }],
    },
  };
  const out = text(await tool.handler({}));
  assert.match(out, /anthropic/);
  assert.match(out, /weekly 62%/, "the weekly figure must be reported");
  assert.match(out, /5h 91%/, "…and the short window, which is the one that bites first");
});

test("A MISSING PERCENTAGE IS '?', NEVER 0% — the assertion this file exists for", async () => {
  // `left_pct: null` means "not known". Printed as 0% it reads as "exhausted", and those two states call
  // for opposite responses from an operator.
  routes = {
    "/api/v1/usage": {
      pools: [{ source_id: "openai", weekly: { left_pct: null }, five_hour: {} }],
    },
  };
  const out = text(await tool.handler({}));
  assert.match(out, /weekly \? left/, "an unknown weekly figure must render as ?");
  assert.match(out, /5h \? left/, "…and so must an absent 5-hour window");
  assert.doesNotMatch(out, /0%/, "an unknown percentage must NEVER be rendered as zero");
});

test("stale and unknown are their own tags, not folded into the number", async () => {
  // A stale pool has real numbers that may no longer be true. Dropping the tag leaves a confident report
  // built on old data, which is worse than an obviously missing one.
  routes = {
    "/api/v1/usage": {
      pools: [{ source_id: "openai", weekly: { left_pct: 40 }, five_hour: { left_pct: 40 }, stale: true, unknown: true }],
    },
  };
  const out = text(await tool.handler({}));
  assert.match(out, /\(stale\)/, "staleness must be visible next to the number it qualifies");
  assert.match(out, /\(unknown\)/);
  assert.match(out, /40%/, "…and the number is still shown, since it is the best available");
});

test("a non-normal severity is surfaced, and a normal one is not noise", async () => {
  routes = { "/api/v1/usage": { pools: [{ source_id: "a", weekly: {}, five_hour: {}, severity: "critical" }] } };
  assert.match(text(await tool.handler({})), /\[critical\]/);
  routes = { "/api/v1/usage": { pools: [{ source_id: "a", weekly: {}, five_hour: {}, severity: "normal" }] } };
  assert.doesNotMatch(text(await tool.handler({})), /\[normal\]/, "the normal case must not be labelled");
});

test("no pools yet is reported as warming up, NOT as zero quota", async () => {
  // The startup case. "No usage data" and "no quota left" must not look alike.
  routes = { "/api/v1/usage": { pools: [] } };
  const out = text(await tool.handler({}));
  assert.match(out, /collector warming up|No usage data/i);
  assert.doesNotMatch(out, /0%/, "an empty collector is not an exhausted pool");
});

test("the caller's own line is BEST-EFFORT: a broken lookup must not cost the pool table", async () => {
  // It reaches a second endpoint. The pool table is the answer; the caller's own row is a convenience, and
  // a failure there must be swallowed rather than turning a useful report into an error.
  routes = { "/api/v1/usage": { pools: [{ source_id: "anthropic", weekly: { left_pct: 50 }, five_hour: {} }] } };
  // No /agents/agent-a route registered, so that call 404s.
  const out = text(await tool.handler({}));
  assert.match(out, /anthropic/, "the pool table must survive a failed per-agent lookup");
  assert.match(out, /weekly 50%/);
  assert.ok(!/undefined|NaN|\[object Object\]/.test(out), `leaked a placeholder: ${out}`);
});

test("when the caller's own quota IS known it is reported, and critical is flagged", async () => {
  routes = {
    "/api/v1/usage": { pools: [{ source_id: "anthropic", weekly: { left_pct: 5 }, five_hour: {} }] },
    "/api/v1/agents/agent-a": { agent: { usageSource: "anthropic", poolWeeklyPctLeft: 5, quotaCritical: true } },
  };
  const out = text(await tool.handler({}));
  assert.match(out, /agent-a/, "the caller must be named");
  assert.match(out, /anthropic: 5% weekly left/);
  // CASE-INSENSITIVE on purpose. The personal line used to say [CRITICAL] while the pool table
  // above it said [critical] -- two spellings of one vocabulary in one message. It now renders
  // the severity the same way the table does, and this assertion pins the FLAG, not the casing.
  assert.match(out, /\[critical\]/i, "a critical quota must be flagged, not left to arithmetic");
});

test("CALL SITE: the caller's own WARNING reaches the output, not just critical", async () => {
  // The mutation that made this necessary: pointing the call site at an empty object left every
  // assertion in the predicates test green while the handler reported nothing. Testing the formatter
  // is not testing that anyone calls it.
  //
  // The shape is verbatim from the live fleet on 2026-08-27, where 21 of 47 agents carried
  // poolSeverity "warning" with poolWeeklyPctLeft 16 -- severity driven by the five-hour window while
  // the line quoted the weekly one, and the warning never appeared at all.
  routes = {
    "/api/v1/usage": { pools: [{ source_id: "openai", weekly: { left_pct: 16 }, five_hour: { left_pct: 4 } }] },
    "/api/v1/agents/agent-a": {
      agent: { usageSource: "openai", poolWeeklyPctLeft: 16, poolSeverity: "warning", quotaCritical: false },
    },
  };
  const out = text(await tool.handler({}));
  assert.match(out, /agent-a/, "the caller must be named");
  assert.match(out, /openai: 16% weekly left/);
  assert.match(out, /\[warning\]/, "the severity computed for this agent never reached the output");
});

test("CALL SITE: a normal pool leaves the caller's line unadorned", async () => {
  // ANTI-VACUITY for the test above: if every line carried a tag, matching one would prove nothing.
  routes = {
    "/api/v1/usage": { pools: [{ source_id: "openai", weekly: { left_pct: 80 }, five_hour: { left_pct: 70 } }] },
    "/api/v1/agents/agent-a": {
      agent: { usageSource: "openai", poolWeeklyPctLeft: 80, poolSeverity: "normal", quotaCritical: false },
    },
  };
  const out = text(await tool.handler({}));
  assert.match(out, /agent-a/);
  const mine = out.split("\n").find((l) => l.includes("agent-a"));
  assert.doesNotMatch(mine, /\[/, `a healthy pool was tagged: ${mine}`);
});

test("it is registered exactly once across the bridge, and reaches only owned leaves", () => {
  const owning = toolSources().filter(([, src]) => /server\.tool\(\s*\n?\s*"comms_usage"/.test(src));
  assert.equal(owning.length, 1, `registered by ${owning.map(([f]) => f).join(", ")}`);
  assert.equal(owning[0][0], "usage-tool.mjs");

  const src = readFileSync(path.join(STDIO_DIR, "usage-tool.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state");
  const imports = [...src.matchAll(/^import .* from "([^"]+)";$/gm)].map((m) => m[1]).sort();
  // `usage-predicates.mjs` is an owned leaf holding the pure line formatter, so the tool module
  // keeps its one-name export surface (asserted above) and the formatter is still unit-testable.
  assert.deepEqual(imports, ["./aify-service-endpoint.mjs", "./launch-identity.mjs", "./usage-predicates.mjs"]);
});

process.on("exit", () => { try { server.close(); } catch { /* best effort */ } });
