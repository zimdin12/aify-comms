// The dispatch claim pass, tested by CALLING it against a real HTTP server.
//
// Extracted from server.js in v0.5.4 — the largest body in the bridge, and until now reachable only by
// starting a bridge, because server.js is imported by no test.
//
// THIS IS THE CLAIM PATH. It asks the service for work on behalf of each agent this bridge hosts and
// then launches it. Every incident this repo records about a restart that produced no worker, or work
// stranded behind a dead one, ran through here.
//
// NOTHING HERE LAUNCHES A RUNTIME. `REMOTE_AGENT_STATE` is the gate: the pass iterates it, so an empty
// map means the pass does nothing at all, and an entry without `info` is skipped by its first line.
// Those are the states asserted here. Driving it far enough to reach `launchRuntimeRun` needs a real
// runtime and belongs in the live round-trip, not in a unit suite that must never spawn anything.

import assert from "node:assert/strict";
import test from "node:test";
import http from "node:http";

const REQUESTS = [];
const SERVER = http.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", () => {
    REQUESTS.push({ method: req.method, url: req.url, body });
    res.writeHead(200, { "content-type": "application/json" });
    res.end("{}");
  });
});
const PORT = await new Promise((r) => SERVER.listen(0, "127.0.0.2", () => r(SERVER.address().port)));

process.env.AIFY_SERVER_URL = `http://127.0.0.2:${PORT}`;
process.env.AIFY_API_KEY = "test-key";
const { runDispatchPass } = await import("../dispatch-loop.mjs");
const { REMOTE_AGENT_STATE, ACTIVE_RUNS } = await import("../bridge-agent-state.mjs");

test.after(() => SERVER.close());

const deps = () => ({
  AUTO_REREGISTER_AFTER_FAILURES: 3,
  CLAIM_OPTS: {},
  CLAIM_WAIT_MS: 0,
  MACHINE_ID: "machine-1",
  reportResidentRuntimeLost: async () => {},
  terminateResidentHost: () => {},
});

function reset() {
  REQUESTS.length = 0;
  REMOTE_AGENT_STATE.clear();
  ACTIVE_RUNS.clear();
}

test("WITH NO AGENTS the pass makes no requests at all", async () => {
  // This runs every 3 seconds in every bridge, resident and environment alike. A pass that claimed on
  // an empty roster would be one request per bridge per tick for nothing.
  reset();
  await runDispatchPass(deps());
  assert.deepEqual(REQUESTS, []);
});

test("an entry with NO `info` is skipped — a half-registered agent must not be claimed for", async () => {
  // `if (!state?.info) continue;`. The map is populated before registration completes, so this state is
  // ordinary rather than exceptional, and claiming for it would take work the agent cannot run.
  reset();
  REMOTE_AGENT_STATE.set("half-registered", {});
  await runDispatchPass(deps());
  assert.deepEqual(REQUESTS, [], "nothing may be claimed for an agent with no info");
});

test("it does not throw on a roster of skippable entries", async () => {
  // The pass has no try/catch of its own — `runDispatchLoop` owns that — so anything escaping here
  // reaches the loop's catch and, before v0.5.4's shutdown gate, could repeat every tick.
  reset();
  REMOTE_AGENT_STATE.set("a", {});
  REMOTE_AGENT_STATE.set("b", { info: null });
  REMOTE_AGENT_STATE.set("c", undefined);
  await assert.doesNotReject(() => runDispatchPass(deps()));
  assert.deepEqual(REQUESTS, []);
});

test("THE SOLO-BRIDGE LONG POLL IS DECIDED BEFORE THE LOOP, from the roster size", async () => {
  // `REMOTE_AGENT_STATE.size <= 1`. A multi-agent environment bridge iterates its agents SEQUENTIALLY,
  // so a long idle wait per agent would serialise and delay every agent behind the first. Deciding it
  // per-agent inside the loop instead would reintroduce exactly that stall on the common path.
  //
  // Asserted structurally because the decision is not observable from outside without launching a
  // runtime: the constant is read once, above the `for`.
  const src = await import("node:fs").then((fs) =>
    fs.readFileSync(new URL("../dispatch-loop.mjs", import.meta.url), "utf8"));
  const decl = src.indexOf("const soloAgentBridge = REMOTE_AGENT_STATE.size <= 1;");
  const loop = src.indexOf("for (const [agentId, state] of REMOTE_AGENT_STATE.entries())");
  assert.notEqual(decl, -1, "the solo-bridge decision must be findable");
  assert.notEqual(loop, -1, "the agent loop must be findable");
  assert.ok(decl < loop, "it must be decided ONCE, before the per-agent loop");
});
