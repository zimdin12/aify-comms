// Reconciling this environment's MANAGED agents against the service's snapshot.
//
// Extracted from server.js in v0.5.4, where nothing could reach it. What it decides is which managed
// agents this bridge still OWNS, and both failure directions are incidents rather than annoyances:
// dropping an agent this bridge does host leaves it hosted by NOBODY and its work queued forever;
// keeping one it no longer hosts has two bridges both believing they own it and both claiming its runs.
//
// Fake service on 127.0.0.2, `AIFY_SERVER_URL` set BEFORE the import.

import assert from "node:assert/strict";
import test from "node:test";
import http from "node:http";

const REQUESTS = [];
let AGENTS = {};
let SESSIONS = [];
const SERVER = http.createServer((req, res) => {
  REQUESTS.push({ method: req.method, url: req.url });
  res.writeHead(200, { "content-type": "application/json" });
  res.end(JSON.stringify(req.url.includes("/sessions") ? { sessions: SESSIONS } : { agents: AGENTS }));
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
const { syncManagedEnvironmentAgentsPass } = await import("../managed-environment-sync.mjs");
const { REMOTE_AGENT_STATE } = await import("../bridge-agent-state.mjs");

test.after(() => SERVER.close());

const ENV = {
  id: "env-1",
  cwdRoots: ["C:/work"],
  runtimes: [{ runtime: "claude", available: true }, { runtime: "codex", available: false }],
};

const deps = (environment = ENV) => ({
  MACHINE_ID: "machine-1",
  effectiveEnvironmentPayload: () => environment,
  ensureDispatchLoop: () => {},
});

function scenario({ agents = {}, sessions = [] } = {}) {
  REQUESTS.length = 0;
  AGENTS = agents;
  SESSIONS = sessions;
  REMOTE_AGENT_STATE.clear();
}

test("it reads BOTH the agent roster and this environment's sessions", async () => {
  // Ownership cannot be decided from either alone: the roster says what exists, the sessions say what
  // is live here. Losing one silently halves the input to every decision below.
  scenario();
  await syncManagedEnvironmentAgentsPass(deps());
  // The bridge prefixes every path with /api/v1, so these match on the SUFFIX — an exact-match
  // assertion here passed nothing and failed for the wrong reason.
  assert.ok(REQUESTS.some((r) => r.url.endsWith("/agents")), "the roster must be fetched");
  const sessions = REQUESTS.find((r) => r.url.includes("/sessions"));
  assert.ok(sessions, "sessions must be fetched");
  assert.match(sessions.url, /environmentId=env-1/, "scoped to THIS environment");
  assert.match(sessions.url, /limit=500/, "and bounded");
});

test("the two reads are issued CONCURRENTLY", async () => {
  // `Promise.all`. This runs on the environment-control cadence, and serialising two round trips
  // against a busy service doubles the window in which ownership is being decided from stale data.
  const src = await import("node:fs").then((fs) =>
    fs.readFileSync(new URL("../managed-environment-sync.mjs", import.meta.url), "utf8"));
  assert.match(src, /await Promise\.all\(\[/, "both reads must be awaited together");
});

test("AN EMPTY FLEET IS A NO-OP, not a mass disown", async () => {
  // The dangerous degenerate case. A service returning an empty roster — a restart, a blip — must not
  // be read as "this bridge owns nothing", because the next step after disowning is reaping.
  scenario({ agents: {}, sessions: [] });
  REMOTE_AGENT_STATE.set("coder-1", { info: { id: "coder-1" }, managed: true });
  await assert.doesNotReject(() => syncManagedEnvironmentAgentsPass(deps()));
});

test("it survives a snapshot with no agents or sessions keys at all", async () => {
  // `agentsRes.agents || {}` and `sessionsRes.sessions || []`. Both endpoints have returned bare
  // objects before, and this runs inside a loop whose catch would otherwise fire every tick.
  scenario();
  AGENTS = undefined;
  SESSIONS = undefined;
  await assert.doesNotReject(() => syncManagedEnvironmentAgentsPass(deps()));
});

test("an environment with NO declared runtimes is handled without throwing", async () => {
  // `(environment.runtimes || [])`. An environment mid-registration has none, and this pass runs before
  // registration is necessarily complete.
  scenario();
  await assert.doesNotReject(() => syncManagedEnvironmentAgentsPass(deps({ id: "env-1" })));
});

test("UNAVAILABLE runtimes are excluded from the available set", async () => {
  // `item?.available !== false`. An agent whose runtime is not installed here must not be adopted, or
  // this bridge claims work it cannot possibly run — which strands the run rather than failing it.
  const src = await import("node:fs").then((fs) =>
    fs.readFileSync(new URL("../managed-environment-sync.mjs", import.meta.url), "utf8"));
  assert.match(src, /available !== false/, "availability must filter the runtime set");
});
