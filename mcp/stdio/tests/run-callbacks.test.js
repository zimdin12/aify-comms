// The callbacks a launched runtime fires back at the bridge — CALLED, for the first time.
//
// A V8-coverage census over `mcp/stdio` reports 122 named functions no test has ever called. Ten of
// them lived in `dispatch-loop.mjs` and these are eight of the ten. They were unreachable by
// CONSTRUCTION: built inside `runDispatchPass` and handed to `launchRuntimeRun`, so reaching one meant
// launching a real runtime. The sibling suite's own docstring said so — "driving it far enough to reach
// launchRuntimeRun needs a real runtime and belongs in the live round-trip". That was true until the
// factory was extracted; it is not true any more, and that docstring is corrected in the same change.
//
// WHY THESE EIGHT ARE WORTH REACHING. `onTurnStart`/`onTurnEnd` are what set and clear `turn_busy` —
// the signal the whole status engine derives `working` from, and the subject of two recorded incidents
// about agents stuck `working` while idle. `onSessionHandleChange` discards a poisoned resume handle;
// get it wrong and an agent can never resume. `onRefs` pins the external thread/turn ids a resume needs.
//
// AND EVERY ONE OF THEM SWALLOWS ITS OWN ERRORS, by design — a callback that throws would kill the
// delivery loop. Combined with "never called by a test", that meant a broken one failed SILENTLY and
// forever. The `catch {}` is correct; being untested was not.
//
// The fake service binds 127.0.0.2, never 127.0.0.1: `defaultFallbackServerUrls` adds the real
// 127.0.0.1:8800 as a fallback for any loopback primary, which is how an earlier test in this repo
// posted to the operator's live service.

import assert from "node:assert/strict";
import test from "node:test";
import http from "node:http";

const REQUESTS = [];
const SERVER = http.createServer((req, res) => {
  let body = "";
  req.on("data", (chunk) => { body += chunk; });
  req.on("end", () => {
    REQUESTS.push({ method: req.method, url: req.url, body: body ? JSON.parse(body) : {} });
    res.writeHead(200, { "content-type": "application/json", connection: "close" });
    res.end("{}");
  });
});
const PORT = await new Promise((r) => SERVER.listen(0, "127.0.0.2", () => r(SERVER.address().port)));
process.env.AIFY_SERVER_URL = `http://127.0.0.2:${PORT}`;
process.env.CLAUDE_MCP_SERVER_URL = "";

// A LITERAL specifier, not a computed path: `moved-names-resolve` scans for the module name and a
// `pathToFileURL(join(...))` is invisible to it, so an extracted module would look untested.
// Still dynamic, because aify-service-endpoint resolves the server URL at LOAD time and the env
// above has to be set first — the same shape as the sibling dispatch-loop.test.js.
const { buildRunCallbacks } = await import("../run-callbacks.mjs");

test.after(() => SERVER.close());

function fixture(overrides = {}) {
  const state = {
    info: {
      id: "cb-agent",
      runtime: "codex",
      runtimeState: { existing: "kept" },
      sessionHandle: "handle-1",
    },
  };
  return {
    agentId: "cb-agent",
    state,
    run: { id: "run-77" },
    runtime: "codex",
    runtimeState: { thread: "t1" },
    ...overrides,
  };
}

function requestsTo(fragment) {
  return REQUESTS.filter((r) => r.url.includes(fragment));
}

async function settle() {
  // `onReady` is fire-and-forget by design — it does not await its own PATCH. Yield until the request
  // arrives rather than sleeping a fixed amount, for the reason this repo has recorded twice: a sleep
  // long enough today is a flake tomorrow.
  for (let i = 0; i < 200 && REQUESTS.length === 0; i += 1) {
    await new Promise((r) => setTimeout(r, 5));
  }
}

test("the factory returns every callback the launcher expects", () => {
  const callbacks = buildRunCallbacks(fixture());
  for (const name of ["onReady", "onEvent", "onRuntimeState", "onRefs", "onTurnStart", "onTurnEnd",
                      "onSessionHandleChange", "terminalSinkProvider"]) {
    assert.equal(typeof callbacks[name], "function", `${name} is missing from the callback bundle`);
  }
});

test("onReady announces readiness as a state distinct from online", async () => {
  REQUESTS.length = 0;
  buildRunCallbacks(fixture()).onReady();
  await settle();
  const [sent] = requestsTo("/agents/cb-agent/ready");
  assert.ok(sent, `no ready PATCH was sent; saw ${JSON.stringify(REQUESTS.map((r) => r.url))}`);
  assert.equal(sent.method, "PATCH");
  assert.equal(sent.body.ready, true);
  assert.equal(sent.body.requestedBy, "controller-handshake",
    "the actor is what tells an operator WHY the agent went ready");
});

test("onEvent appends the runtime's output to the run it belongs to", async () => {
  REQUESTS.length = 0;
  await buildRunCallbacks(fixture()).onEvent("stdout", "hello from the runtime");
  const [sent] = requestsTo("/dispatch/runs/run-77");
  assert.ok(sent, "runtime output never reached the run");
  assert.equal(sent.body.appendEvent, "hello from the runtime");
  assert.equal(sent.body.eventType, "stdout");
});

test("onRuntimeState MERGES rather than replaces, so an existing key survives", async () => {
  REQUESTS.length = 0;
  const args = fixture();
  await buildRunCallbacks(args).onRuntimeState({ added: "new" });
  const [sent] = requestsTo("/agents/cb-agent/runtime-state");
  assert.ok(sent, "the runtime state was never reported");
  assert.deepEqual(sent.body.runtimeState, { existing: "kept", added: "new" },
    "a replace instead of a merge silently discards whatever the runtime had already reported");
  assert.deepEqual(args.state.info.runtimeState, { existing: "kept", added: "new" },
    "the in-memory state must be updated too, or the bridge and the service disagree");
});

test("onRefs sends the external ids a resume needs, and sends NOTHING when it has none", async () => {
  REQUESTS.length = 0;
  await buildRunCallbacks(fixture()).onRefs({ threadId: "th-1", turnId: "tu-1" });
  const [sent] = requestsTo("/dispatch/runs/run-77");
  assert.ok(sent, "the external refs were never recorded");
  assert.equal(sent.body.externalThreadId, "th-1");
  assert.equal(sent.body.externalTurnId, "tu-1");

  // The empty case is the one worth pinning: an unconditional PATCH would overwrite good ids with
  // undefined every time a runtime reported nothing.
  REQUESTS.length = 0;
  await buildRunCallbacks(fixture()).onRefs({});
  assert.equal(requestsTo("/dispatch/runs/run-77").length, 0,
    "onRefs sent a PATCH with no refs in it");
});

test("onTurnStart and onTurnEnd set and clear the signal the status engine reads", async () => {
  REQUESTS.length = 0;
  const callbacks = buildRunCallbacks(fixture());
  await callbacks.onTurnStart();
  await callbacks.onTurnEnd();
  const beats = REQUESTS.filter((r) => r.url.includes("/heartbeat"));
  assert.ok(beats.length >= 2, `expected a busy and a not-busy report, saw ${beats.length}`);
  assert.equal(beats[0].body.turnBusy, true, "onTurnStart did not report the turn as busy");
  assert.equal(beats.at(-1).body.turnBusy, false, "onTurnEnd did not clear turn_busy");
  assert.equal(beats[0].body.turnRunId, "run-77",
    "the busy report must name the run, or a later clear cannot be matched to it");
});

test("onSessionHandleChange CLEARS a poisoned handle, with the reason", async () => {
  REQUESTS.length = 0;
  const args = fixture();
  await buildRunCallbacks(args).onSessionHandleChange("", { reason: "unloadable", previous: "handle-1" });
  const [sent] = requestsTo("/agents/cb-agent/session-handle");
  assert.ok(sent, "a discarded session handle was never cleared on the service");
  assert.equal(sent.body.sessionHandle, "",
    "the handle must be cleared explicitly — a poisoned handle that survives means an agent that can "
    + "never resume");
  assert.equal(args.state.info.sessionHandle, "",
    "the in-memory handle still holds the poisoned value");
});

test("onSessionHandleChange PERSISTS a new handle through re-registration", async () => {
  // THE OTHER BRANCH, and it is here because an exhaustive scan for unresolved names found
  // `reregisterAgentFromState` missing from the extracted module's imports — in code my first tests
  // never executed. The empty-handle path was covered and this one was not, so the extraction would
  // have thrown ReferenceError the first time a runtime reported a NEW handle in production.
  REQUESTS.length = 0;
  const args = fixture();
  await buildRunCallbacks(args).onSessionHandleChange("handle-2", {});
  assert.equal(args.state.info.sessionHandle, "handle-2",
    "a newly discovered session handle was not recorded, so the next run cannot resume it");
});

test("terminalSinkProvider is callable and does not throw for an unknown agent", async () => {
  REQUESTS.length = 0;
  const callbacks = buildRunCallbacks(fixture());
  await assert.doesNotReject(
    () => Promise.resolve(callbacks.terminalSinkProvider({ agentId: "cb-agent", agentInfo: {} })),
    "the sink provider throwing would take the delivery loop down with it",
  );
});

test("EVERY callback swallows a service failure rather than killing the loop", async () => {
  // The design property that makes these dangerous to leave untested: they are best-effort, so a
  // broken one fails silently. Point them at a port with nothing on it and require none to reject.
  const previous = process.env.AIFY_SERVER_URL;
  process.env.AIFY_SERVER_URL = "http://127.0.0.2:1";
  try {
    const callbacks = buildRunCallbacks(fixture());
    await assert.doesNotReject(async () => {
      callbacks.onReady();
      await callbacks.onEvent("stdout", "x");
      await callbacks.onRuntimeState({ a: 1 });
      await callbacks.onRefs({ threadId: "t" });
      await callbacks.onTurnStart();
      await callbacks.onTurnEnd();
      await callbacks.onSessionHandleChange("", { reason: "r" });
      await callbacks.terminalSinkProvider({ agentId: "cb-agent", agentInfo: { runtime: "codex" } });
    }, "a callback propagated a service failure; that would kill the delivery loop");
  } finally {
    process.env.AIFY_SERVER_URL = previous;
  }
});
