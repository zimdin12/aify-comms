// Re-registering an agent from the state the bridge already holds — and the two traps in the payload.
//
// Fourth cluster off the V8-coverage census: `reregisterAgentFromState` had a zero call count. It is the
// path a bridge takes on its OWN initiative when the service answers 404 for an agent it is tracking —
// a service restart with a lost row, most often — so it is what stands between "the fleet re-converges
// by itself" and "every agent goes quiet until someone re-registers it by hand".
//
// TRAP ONE: THE UNEXPANDED PLACEHOLDER. `AIFY_TERMINAL_ID` is exported by the wrapper, and a wrapper
// that passed `${AIFY_TERMINAL_ID}` without expanding it hands this process the literal 21-character
// string. It is truthy, so it would be registered as a terminal id no console can address — the exact
// incident `launch-identity.mjs` was written around for `${AIFY_AGENT_ID}`. `cleanEnvPlaceholder` turns
// it into "", which the server correctly reads as "no terminal". The module's own comment says the two
// registration paths sit together BECAUSE both must sanitise it, and that two copies is how one gets
// fixed and the other does not — so this is asserted here, on the second copy.
//
// TRAP TWO: RESURRECTING A DELETED AGENT. A 410 means the operator removed it deliberately. A re-register
// that retried, or that treated 410 as a transient failure, would bring back an agent someone chose to
// delete — so 410 makes the bridge FORGET it instead, and that is observable in the tracking Maps.
//
// A REAL SERVICE on 127.0.0.2, set before the import: `httpCall` resolves its URL at module load.

import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

const REQUESTS = [];
let STATUS = 200;

const SERVER = http.createServer((req, res) => {
  let body = "";
  req.on("data", (chunk) => { body += chunk; });
  req.on("end", () => {
    REQUESTS.push({ method: req.method, url: req.url, body });
    res.writeHead(STATUS, { "Content-Type": "application/json" });
    res.end(JSON.stringify(STATUS === 200 ? { ok: true } : { detail: "gone" }));
  });
});
const PORT = await new Promise((resolve) => {
  SERVER.listen(0, "127.0.0.2", () => resolve(SERVER.address().port));
});
process.env.AIFY_SERVER_URL = `http://127.0.0.2:${PORT}`;
// The modules read `CLAUDE_MCP_SERVER_URL || AIFY_SERVER_URL` — the LEGACY name WINS, and a
// live wrapper environment exports it. Setting only the new name left the fake below unused.
process.env.CLAUDE_MCP_SERVER_URL = `http://127.0.0.2:${PORT}`;
delete process.env.AIFY_TERMINAL_ID;
delete process.env.AIFY_MANAGED_VIA_WRAPPER;

const { reregisterAgentFromState } = await import("../auto-registration.mjs");
const { ACTIVE_RUNS, CONSECUTIVE_FAILURES, REMOTE_AGENT_STATE } =
  await import("../bridge-agent-state.mjs");

test.after(() => SERVER.close());

function reset({ status = 200 } = {}) {
  REQUESTS.length = 0;
  STATUS = status;
  delete process.env.AIFY_TERMINAL_ID;
  delete process.env.AIFY_MANAGED_VIA_WRAPPER;
}

function state(info = {}) {
  return { info: { role: "coder", runtime: "claude-code", ...info } };
}

function posted() {
  const calls = REQUESTS.filter((r) => r.method === "POST" && r.url.endsWith("/agents"));
  assert.equal(calls.length, 1, `expected one register POST, got ${calls.length}`);
  return JSON.parse(calls[0].body);
}

// ── nothing to re-register from ─────────────────────────────────────────────────────────────────

test("NO cached info means no request at all", async () => {
  // The bridge only re-registers from state it actually holds. Posting a skeleton would create an agent
  // with none of its role, workspace or capabilities — worse than leaving the 404 alone.
  reset();
  for (const bad of [undefined, null, {}, { info: null }]) {
    assert.equal(await reregisterAgentFromState("agent-a", bad), false, JSON.stringify(bad));
  }
  assert.deepEqual(REQUESTS, []);
});

// ── the placeholder trap ────────────────────────────────────────────────────────────────────────

test("an UNEXPANDED ${AIFY_TERMINAL_ID} is sanitised to empty", async () => {
  // Truthy, 21 characters, and registered verbatim it binds a terminal no console can address. Same
  // incident shape as the launch-identity one, on the second of the two paths that must sanitise it.
  reset();
  process.env.AIFY_TERMINAL_ID = "${AIFY_TERMINAL_ID}";
  assert.equal(await reregisterAgentFromState("agent-a", state({ terminalId: "" })), true);
  assert.equal(posted().terminalId, "");
});

test("a REAL terminal id from the environment is preferred over the cached one", async () => {
  // `AIFY_TERMINAL_ID` is stable for the bridge process lifetime, so it is the fresher of the two.
  reset();
  process.env.AIFY_TERMINAL_ID = "vterm_live";
  await reregisterAgentFromState("agent-a", state({ terminalId: "vterm_cached" }));
  assert.equal(posted().terminalId, "vterm_live");
});

test("the CACHED terminal id is the fallback, and it is sanitised too", async () => {
  // R8: a 404 auto-re-register must not drop the console binding. The cached value went through the
  // same wrapper env once, so it can carry the same placeholder.
  reset();
  await reregisterAgentFromState("agent-a", state({ terminalId: "vterm_cached" }));
  assert.equal(posted().terminalId, "vterm_cached");

  reset();
  await reregisterAgentFromState("agent-a", state({ terminalId: "${AIFY_TERMINAL_ID}" }));
  assert.equal(posted().terminalId, "");
});

test("a placeholder-SHAPED but real value is not sanitised away", async () => {
  // The guard is anchored: only a whole string that is exactly `${...}` is a placeholder. A terminal id
  // that merely contains braces is a real id and must survive.
  reset();
  await reregisterAgentFromState("agent-a", state({ terminalId: "vterm_${x}_1" }));
  assert.equal(posted().terminalId, "vterm_${x}_1");
});

// ── the payload the server relies on ────────────────────────────────────────────────────────────

test("the cached identity is sent as-is", async () => {
  reset();
  await reregisterAgentFromState("agent-a", state({
    role: "tester", name: "Tester One", cwd: "/w", model: "opus", runtime: "codex",
    sessionMode: "managed", sessionHandle: "thread-1", capabilities: ["managed-run"],
    runtimeConfig: { appServerUrl: "ws://x" },
  }));
  const body = posted();
  assert.equal(body.agentId, "agent-a");
  assert.equal(body.role, "tester");
  assert.equal(body.name, "Tester One");
  assert.equal(body.cwd, "/w");
  assert.equal(body.runtime, "codex");
  assert.equal(body.sessionMode, "managed");
  assert.equal(body.sessionHandle, "thread-1");
  assert.deepEqual(body.capabilities, ["managed-run"]);
  assert.deepEqual(body.runtimeConfig, { appServerUrl: "ws://x" });
});

test("MISSING fields get defaults rather than nulls", async () => {
  // Re-registration must produce a complete agent. A null role or runtime reaching the service is a row
  // that fails every later capability derivation, which surfaces as an agent nothing can drive.
  reset();
  await reregisterAgentFromState("agent-a", { info: {} });
  const body = posted();
  assert.equal(body.role, "generic");
  assert.equal(body.name, "agent-a");
  assert.equal(body.runtime, "generic");
  assert.equal(body.launchMode, "detached");
  assert.equal(body.sessionMode, "resident");
  assert.equal(body.cwd, "");
  assert.deepEqual(body.capabilities, []);
  assert.deepEqual(body.runtimeConfig, {});
});

test("the name defaults to the AGENT ID, not to a blank", async () => {
  // It is what the dashboard renders. A blank name shows an unlabelled row an operator cannot match to
  // anything.
  reset();
  await reregisterAgentFromState("agent-zed", { info: {} });
  assert.equal(posted().name, "agent-zed");
});

test("it is marked AUTO-REGISTER and carries the bridge's start time", async () => {
  // The tombstone-resurrection guard on the server side reads both: a 404 auto-re-register from a
  // lingering bridge must not resurrect a deliberately-removed agent unless this bridge launched AFTER
  // the deletion. Dropping either field turns that guard off from the outside.
  reset();
  await reregisterAgentFromState("agent-a", state());
  const body = posted();
  assert.equal(body.autoRegister, true);
  assert.ok(body.bridgeStartedAt, "no bridgeStartedAt — the resurrection guard has nothing to compare");
});

test("it identifies the BRIDGE and the MACHINE", async () => {
  // Ownership: the server binds the agent to this bridge instance, and a re-register that omitted it
  // would leave the row owned by whichever bridge registered it last.
  reset();
  await reregisterAgentFromState("agent-a", state());
  const body = posted();
  assert.ok(body.bridgeId, "no bridgeId");
  assert.ok(body.machineId, "no machineId");
});

test("a cached machineId WINS over this host's", async () => {
  // The cached value is what the agent registered with; this bridge may be re-registering on its behalf
  // from a different resolution of the same host.
  reset();
  await reregisterAgentFromState("agent-a", state({ machineId: "linux:recorded" }));
  assert.equal(posted().machineId, "linux:recorded");
});

test("MANAGED-WRAPPER-CHILD is true from the environment OR from cached state", async () => {
  // Either source is sufficient, and the env one is compared to the exact string "1" — a wrapper that
  // exported "true" or "yes" must not flip an agent's delivery model.
  reset();
  process.env.AIFY_MANAGED_VIA_WRAPPER = "1";
  await reregisterAgentFromState("agent-a", state());
  assert.equal(posted().managedWrapperChild, true);

  reset();
  await reregisterAgentFromState("agent-a", state({ managedWrapperChild: true }));
  assert.equal(posted().managedWrapperChild, true);

  reset();
  process.env.AIFY_MANAGED_VIA_WRAPPER = "true";
  await reregisterAgentFromState("agent-a", state());
  assert.equal(posted().managedWrapperChild, false,
    'only the exact string "1" enables it from the environment');
});

// ── the 410 refusal ─────────────────────────────────────────────────────────────────────────────

test("a 410 makes the bridge FORGET the agent instead of retrying", async () => {
  // The operator deleted it. A re-register that treated 410 as failure would try again on the next 404
  // and keep resurrecting an agent somebody chose to remove.
  REMOTE_AGENT_STATE.set("agent-gone", { info: { role: "coder" } });
  ACTIVE_RUNS.set("agent-gone", { runId: "r1" });
  CONSECUTIVE_FAILURES.set("agent-gone", 3);

  reset({ status: 410 });
  assert.equal(await reregisterAgentFromState("agent-gone", state()), false);

  assert.equal(REMOTE_AGENT_STATE.has("agent-gone"), false, "still tracked after a 410");
  assert.equal(ACTIVE_RUNS.has("agent-gone"), false, "an active run survived the tombstone");
  assert.equal(CONSECUTIVE_FAILURES.has("agent-gone"), false, "a failure counter survived");
});

test("another agent's tracking is untouched by a 410", async () => {
  REMOTE_AGENT_STATE.set("agent-keep", { info: { role: "coder" } });
  REMOTE_AGENT_STATE.set("agent-drop", { info: { role: "coder" } });

  reset({ status: 410 });
  await reregisterAgentFromState("agent-drop", state());

  assert.equal(REMOTE_AGENT_STATE.has("agent-keep"), true);
  REMOTE_AGENT_STATE.delete("agent-keep");
});

test("an ORDINARY failure returns false WITHOUT forgetting the agent", async () => {
  // The distinction that matters: a 500 or a connection reset is transient, and forgetting the agent
  // would stop the bridge tracking something that is merely unreachable this second.
  REMOTE_AGENT_STATE.set("agent-transient", { info: { role: "coder" } });

  reset({ status: 500 });
  assert.equal(await reregisterAgentFromState("agent-transient", state()), false);
  assert.equal(REMOTE_AGENT_STATE.has("agent-transient"), true,
    "a transient failure dropped the agent from tracking");
  REMOTE_AGENT_STATE.delete("agent-transient");
});

test("SUCCESS is reported as true", async () => {
  reset();
  assert.equal(await reregisterAgentFromState("agent-a", state()), true);
});
