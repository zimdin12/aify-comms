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
// What the fake service answers, by request. Empty JSON unless a test says otherwise.
let RESPOND = () => ({});
const SERVER = http.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", () => {
    REQUESTS.push({ method: req.method, url: req.url, body });
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(RESPOND(req)));
  });
});
const PORT = await new Promise((r) => SERVER.listen(0, "127.0.0.2", () => r(SERVER.address().port)));

process.env.AIFY_SERVER_URL = `http://127.0.0.2:${PORT}`;

// The modules read `CLAUDE_MCP_SERVER_URL || AIFY_SERVER_URL` — the LEGACY name WINS, and a

// live wrapper environment exports it. Setting only the new name left the fake below unused.

process.env.CLAUDE_MCP_SERVER_URL = `http://127.0.0.2:${PORT}`;
process.env.AIFY_API_KEY = "test-key";
// Paired with the LEGACY name, which the modules read FIRST: a wrapper environment exports it,
// and leaving it set means the module sends the operator's real key instead of this one.
process.env.CLAUDE_MCP_API_KEY = "test-key";
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
  RESPOND = () => ({});
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

// THE RECORD FETCH RUNS ONLY WHEN THE HEARTBEAT'S REVISION MOVES. It ran every tick -- every 3 s on a
// managed hermes bridge that never long-polls a claim -- to learn nothing (measured 2026-09-18).
const REV_AGENT = { info: { agentId: "rev-agent", runtime: "generic", sessionMode: "resident" } };

async function agentGetsPerPass(revisionOf, passes) {
  const gets = [];
  RESPOND = (req) => (req.url.endsWith("/heartbeat") ? { ok: true, agentRevision: revisionOf() } : {});
  for (let i = 0; i < passes; i += 1) {
    const before = REQUESTS.length;
    await runDispatchPass(deps());
    // The heartbeat is fire-and-forget; let its answer land before the next tick, as it does live.
    for (let wait = 0; wait < 100 && REMOTE_AGENT_STATE.get("rev-agent").agentRevision !== revisionOf(); wait += 1) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    gets.push(REQUESTS.slice(before).filter((r) => r.method === "GET" && r.url.endsWith("/agents/rev-agent")).length);
  }
  return gets;
}

test("an unchanged record is not fetched again once its revision is known", async () => {
  reset();
  REMOTE_AGENT_STATE.set("rev-agent", structuredClone(REV_AGENT));
  // Pass 1 has no revision yet; pass 2 learns r1 is newer than the fetch it made; then none.
  assert.deepEqual(await agentGetsPerPass(() => "r1", 5), [1, 1, 0, 0, 0]);
});

test("CONTROL: a record whose revision moves (a Stop, a mode switch) is fetched again", async () => {
  reset();
  REMOTE_AGENT_STATE.set("rev-agent", structuredClone(REV_AGENT));
  let revision = "r1";
  assert.deepEqual(await agentGetsPerPass(() => revision, 3), [1, 1, 0]);
  revision = "r2";
  assert.deepEqual(await agentGetsPerPass(() => revision, 3), [0, 1, 0],
    "the tick after the revision moved must re-read the record, and only that tick");
});

test("CONTROL: a service that sends no revision keeps the old fetch-every-tick behaviour", async () => {
  reset();
  REMOTE_AGENT_STATE.set("rev-agent", structuredClone(REV_AGENT));
  const gets = [];
  for (let i = 0; i < 3; i += 1) {
    const before = REQUESTS.length;
    await runDispatchPass(deps());
    gets.push(REQUESTS.slice(before).filter((r) => r.method === "GET" && r.url.endsWith("/agents/rev-agent")).length);
  }
  assert.deepEqual(gets, [1, 1, 1]);
});
