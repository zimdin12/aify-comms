#!/usr/bin/env node
// Unit tests for the per-agent managed-hermes HIDDEN HELPER (hermes-managed-host.js).
//
// The managed-host is the hermes analogue of claude-channel.js for the
// visible-TUI delivery model:
//   1. ensureGatewayHost: spawn a HIDDEN `hermes dashboard --tui --port <P>`
//      gateway host (windowsHide:true — no popup window), wait until the
//      dashboard index responds, scrape __HERMES_SESSION_TOKEN__, return
//      { port, token, wsUrl }.
//   2. deliverRun (claim/deliver loop): claim a dispatch run → reportTurnBusy →
//      open a WS to the gateway → session.active_list → pickSessionForKey to
//      resolve the visible TUI's EPHEMERAL runtime sid for `aify-<agentId>` →
//      prompt.submit {session_id, text} → on 4009 busy fall back to
//      session.steer → markRunDelivered (WAKE-ONLY: NO reply posted; the
//      in-session agent self-replies via comms_send) → clearTurn.
//   3. teardown: on SIGTERM/SIGINT/release kill the gateway-host child + exit.
//
// The runtime sid is NEVER cached: each delivery re-runs session.active_list.
//
// Tests inject a fake aify httpCall, a fake WS client modeling
// session.active_list / prompt.submit / busy(4009)+steer, and a fake spawn +
// fake index HTML for ensureGatewayHost.

import assert from "node:assert/strict";
import { test } from "node:test";
import { EventEmitter } from "node:events";
import {
  ensureGatewayHost,
  deliverRun,
  waitForActiveSession,
  runPollCycle,
  teardownGatewayHost,
  installShutdownTeardown,
  runEnsureHostCli,
  runDeliveryLoop,
  runCli,
  ensureStableSession,
  resolveHermesPython,
} from "../hermes-managed-host.js";

// ---------------------------------------------------------------------------
// Fakes
// ---------------------------------------------------------------------------

// A recording fake of the aify httpCall(method, endpoint, body) helper.
function makeAifyHttp({ claims = [], release = false } = {}) {
  const calls = [];
  let claimIdx = 0;
  async function httpCall(method, endpoint, body = null) {
    calls.push({ method, endpoint, body });
    if (method === "POST" && endpoint === "/dispatch/claim") {
      if (release) return { ok: true, run: null, release: true };
      const run = claims[claimIdx++];
      return { run: run || null };
    }
    return { ok: true };
  }
  return { httpCall, calls };
}

function findCall(calls, method, matcher) {
  return calls.find(
    (c) => c.method === method && (typeof matcher === "function" ? matcher(c.endpoint) : c.endpoint === matcher),
  );
}

// A fake WS client. Models the gateway JSON-RPC over a request/response method
// map. `behaviors[method]` is either a result object or a function(params)->
// result, or it may throw (e.g. a 4009 busy error). Records every frame sent.
function makeFakeWsClient(behaviors = {}) {
  const sent = [];
  let closed = false;
  return {
    sent,
    get closed() {
      return closed;
    },
    async request(frame) {
      sent.push(frame);
      const method = frame.method;
      const b = behaviors[method];
      if (typeof b === "function") return b(frame.params, frame);
      if (b instanceof Error) throw b;
      return b ?? {};
    },
    close() {
      closed = true;
    },
  };
}

const ACTIVE_LIST_RESULT = {
  result: {
    sessions: [
      { id: "live-sid-ab12", session_key: "aify-sc-hermes", status: "ready", started_at: "2026-05-31T10:00:00Z" },
    ],
  },
};

const SAMPLE_RUN = {
  id: "run-1",
  messageId: "msg-1",
  from: "sc-manager",
  subject: "status?",
  body: "What is the build status?",
  priority: "normal",
  executionMode: "channel",
  requireReply: true,
};

// ---------------------------------------------------------------------------
// deliverRun
// ---------------------------------------------------------------------------

test("deliverRun: active_list resolves the live TUI sid, prompt.submit targets it, marks delivered, NO reply", async () => {
  const { httpCall, calls } = makeAifyHttp();
  const ws = makeFakeWsClient({
    "session.active_list": ACTIVE_LIST_RESULT,
    "prompt.submit": { status: "streaming" },
  });

  await deliverRun({
    run: SAMPLE_RUN,
    agentId: "sc-hermes",
    httpCall,
    wsClient: ws,
  });

  // active_list was called, then prompt.submit against the RESOLVED ephemeral sid.
  const activeList = ws.sent.find((f) => f.method === "session.active_list");
  assert.ok(activeList, "expected a session.active_list frame");
  const submit = ws.sent.find((f) => f.method === "prompt.submit");
  assert.ok(submit, "expected a prompt.submit frame");
  assert.equal(submit.params.session_id, "live-sid-ab12", "must submit to the LIVE runtime sid");
  assert.ok(submit.params.text.includes("build status"), "prompt text carries the dispatch body");

  // WAKE-ONLY: no reply posted.
  assert.ok(!findCall(calls, "POST", "/messages/send"), "must NOT post a reply (agent self-replies)");

  // Run PATCHed delivered.
  const patch = findCall(calls, "PATCH", (e) => e.startsWith("/dispatch/runs/"));
  assert.ok(patch, "expected a PATCH /dispatch/runs/<id>");
  assert.equal(String(patch.body.status), "delivered");

  // turn_busy pulsed and SUSTAINED. prompt.submit is FIRE-AND-FORGET (returns on
  // accept, not completion), so the visible-TUI turn is only just STARTING —
  // clearing here would lose "working" for the entire turn (operator-reported
  // 2026-05-31: managed hermes never showed working). Mirror claude-channel.js:
  // leave turn_busy set; the server 120s stale window + the agent's reply close it.
  const hb = findCall(calls, "POST", (e) => e.endsWith("/heartbeat"));
  assert.ok(hb, "expected a heartbeat");
  assert.equal(hb.body.turnBusy, true, "delivery must pulse turn_busy=true");
  assert.ok(
    !findCall(calls, "POST", (e) => e.endsWith("/turn-end")),
    "must NOT clear turn on a successful delivery (the fire-and-forget turn is just starting)",
  );
});

test("deliverRun: busy 4009 on prompt.submit → falls back to session.steer", async () => {
  const { httpCall, calls } = makeAifyHttp();
  const busy = Object.assign(new Error("session busy"), { code: 4009 });
  const ws = makeFakeWsClient({
    "session.active_list": ACTIVE_LIST_RESULT,
    "prompt.submit": busy,
    "session.steer": { status: "queued" },
  });

  await deliverRun({ run: SAMPLE_RUN, agentId: "sc-hermes", httpCall, wsClient: ws });

  const steer = ws.sent.find((f) => f.method === "session.steer");
  assert.ok(steer, "expected a session.steer fallback frame on 4009 busy");
  assert.equal(steer.params.session_id, "live-sid-ab12", "steer targets the live runtime sid");

  // Still settled delivered.
  const patch = findCall(calls, "PATCH", (e) => e.startsWith("/dispatch/runs/"));
  assert.equal(String(patch.body.status), "delivered");
});

test("deliverRun: never caches the sid — re-runs active_list on every delivery", async () => {
  const { httpCall } = makeAifyHttp();
  // First delivery sees sid A; second sees a DIFFERENT ephemeral sid B.
  const lists = [
    { result: { sessions: [{ id: "sid-A", session_key: "aify-sc-hermes" }] } },
    { result: { sessions: [{ id: "sid-B", session_key: "aify-sc-hermes" }] } },
  ];
  let i = 0;
  const ws = makeFakeWsClient({
    "session.active_list": () => lists[i++],
    "prompt.submit": { status: "streaming" },
  });

  await deliverRun({ run: SAMPLE_RUN, agentId: "sc-hermes", httpCall, wsClient: ws });
  await deliverRun({ run: { ...SAMPLE_RUN, id: "run-2" }, agentId: "sc-hermes", httpCall, wsClient: ws });

  const submits = ws.sent.filter((f) => f.method === "prompt.submit");
  assert.equal(submits.length, 2);
  assert.equal(submits[0].params.session_id, "sid-A");
  assert.equal(submits[1].params.session_id, "sid-B", "second delivery must re-discover, not reuse sid-A");
});

test("deliverRun: COLD START — active_list empty then key appears → waits, then submits (no failure)", async () => {
  const { httpCall, calls } = makeAifyHttp();
  // First two active_list polls return empty (TUI still resuming), third has the key.
  const lists = [
    { result: { sessions: [] } },
    { result: { sessions: [] } },
    ACTIVE_LIST_RESULT,
  ];
  let i = 0;
  const ws = makeFakeWsClient({
    "session.active_list": () => lists[Math.min(i++, lists.length - 1)],
    "prompt.submit": { status: "streaming" },
  });

  await deliverRun({
    run: SAMPLE_RUN,
    agentId: "sc-hermes",
    httpCall,
    wsClient: ws,
    // Tight timing so the test doesn't actually sleep for the real window.
    attachWaitMs: 10000,
    attachPollMs: 1,
    sleepImpl: async () => {},
  });

  // It polled active_list more than once (waited for attach), then submitted.
  const lists_sent = ws.sent.filter((f) => f.method === "session.active_list");
  assert.ok(lists_sent.length >= 3, `expected multiple active_list polls, got ${lists_sent.length}`);
  const submit = ws.sent.find((f) => f.method === "prompt.submit");
  assert.ok(submit, "must submit once the TUI session attaches");
  assert.equal(submit.params.session_id, "live-sid-ab12");

  // Settled DELIVERED, not failed/requeued.
  const patch = findCall(calls, "PATCH", (e) => e.startsWith("/dispatch/runs/"));
  assert.equal(String(patch.body.status), "delivered");
});

test("deliverRun: TUI never attaches within window → run REQUEUED (claimable), NOT failed, no submit", async () => {
  const { httpCall, calls } = makeAifyHttp();
  const ws = makeFakeWsClient({
    "session.active_list": { result: { sessions: [] } },
  });

  await assert.doesNotReject(() =>
    deliverRun({
      run: SAMPLE_RUN,
      agentId: "sc-hermes",
      httpCall,
      wsClient: ws,
      attachWaitMs: 5,
      attachPollMs: 1,
      sleepImpl: async () => {},
    }),
  );
  const patch = findCall(calls, "PATCH", (e) => e.startsWith("/dispatch/runs/"));
  assert.ok(patch, "expected a PATCH /dispatch/runs/<id>");
  // Transient not-yet-attached MUST requeue (stay claimable), never permanently fail.
  assert.equal(String(patch.body.status), "queued", "must requeue, not fail, on transient not-attached");
  // No prompt.submit attempted when there's no session.
  assert.ok(!ws.sent.find((f) => f.method === "prompt.submit"));
  // No delivery happened → the turn_busy pulse must be cleared (not left
  // falsely "working" while the run sits requeued).
  assert.ok(
    findCall(calls, "POST", (e) => e.endsWith("/turn-end")),
    "requeue (no delivery) must clear the turn_busy pulse",
  );
});

test("waitForActiveSession: returns the sid as soon as the key appears", async () => {
  const lists = [{ result: { sessions: [] } }, ACTIVE_LIST_RESULT];
  let i = 0;
  const ws = makeFakeWsClient({
    "session.active_list": () => lists[Math.min(i++, lists.length - 1)],
  });
  let id = 1;
  const sid = await waitForActiveSession({
    wsClient: ws,
    key: "aify-sc-hermes",
    nextId: () => id++,
    deadlineMs: 1000,
    intervalMs: 1,
    sleepImpl: async () => {},
    log: () => {},
  });
  assert.equal(sid, "live-sid-ab12");
});

test("waitForActiveSession: returns null after the deadline when the key never appears", async () => {
  const ws = makeFakeWsClient({ "session.active_list": { result: { sessions: [] } } });
  let id = 1;
  const sid = await waitForActiveSession({
    wsClient: ws,
    key: "aify-sc-hermes",
    nextId: () => id++,
    deadlineMs: 5,
    intervalMs: 1,
    sleepImpl: async () => {},
    log: () => {},
  });
  assert.equal(sid, null);
});

// ---------------------------------------------------------------------------
// runPollCycle
// ---------------------------------------------------------------------------

test("runPollCycle: claims a channel run, delivers it, settles delivered", async () => {
  const { httpCall, calls } = makeAifyHttp({ claims: [SAMPLE_RUN] });
  const ws = makeFakeWsClient({
    "session.active_list": ACTIVE_LIST_RESULT,
    "prompt.submit": { status: "streaming" },
  });

  const result = await runPollCycle({
    agentId: "sc-hermes",
    machineId: "m",
    bridgeId: "b",
    httpCall,
    wsClient: ws,
  });

  const claim = findCall(calls, "POST", "/dispatch/claim");
  assert.ok(claim);
  assert.equal(claim.body.agentId, "sc-hermes");
  assert.equal(claim.body.bridgeKind, "channel-sidecar");
  assert.ok(claim.body.executionModes.includes("channel"));
  assert.ok(claim.body.executionModes.includes("resident"));

  assert.equal(result.processed, 1);
  const patch = findCall(calls, "PATCH", (e) => e.startsWith("/dispatch/runs/"));
  assert.equal(String(patch.body.status), "delivered");
});

test("runPollCycle: release signal → stops driving, reports released", async () => {
  const { httpCall, calls } = makeAifyHttp({ release: true });
  const ws = makeFakeWsClient({ "session.active_list": ACTIVE_LIST_RESULT });

  const result = await runPollCycle({ agentId: "sc-hermes", machineId: "m", bridgeId: "b", httpCall, wsClient: ws });

  assert.ok(result.released, "must report released=true");
  assert.equal(result.processed, 0);
  assert.ok(!findCall(calls, "PATCH", (e) => e.startsWith("/dispatch/runs/")), "released → no run settled");
  assert.ok(!ws.sent.find((f) => f.method === "prompt.submit"));
});

// ---------------------------------------------------------------------------
// ensureGatewayHost
// ---------------------------------------------------------------------------

const FAKE_INDEX_HTML = `<html><script>window.__HERMES_SESSION_TOKEN__="tok-abc123";</script></html>`;

function makeFakeSpawn() {
  const spawns = [];
  function spawn(cmd, args, opts) {
    const child = new EventEmitter();
    child.pid = 4242;
    child.kill = () => {
      child._killed = true;
    };
    child.unref = () => {};
    spawns.push({ cmd, args, opts, child });
    return child;
  }
  return { spawn, spawns };
}

// Fake fetch returning the index HTML once the host is "up". Models a couple of
// connection failures before the dashboard binds.
function makeFakeFetch({ failTimes = 0, html = FAKE_INDEX_HTML } = {}) {
  let calls = 0;
  return async function fetchImpl() {
    calls += 1;
    if (calls <= failTimes) {
      throw new Error("ECONNREFUSED");
    }
    return {
      ok: true,
      status: 200,
      async text() {
        return html;
      },
    };
  };
}

test("ensureGatewayHost: spawns hermes dashboard --tui with windowsHide:true and scrapes the token", async () => {
  const { spawn, spawns } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch();

  const out = await ensureGatewayHost({
    agentId: "sc-hermes",
    port: 8765,
    hermesCmd: "hermes",
    spawn,
    fetchImpl,
    probeFirst: false,
    readyIntervalMs: 1,
  });

  assert.equal(spawns.length, 1, "must spawn exactly one gateway host");
  const { cmd, args, opts } = spawns[0];
  assert.equal(cmd, "hermes");
  assert.ok(args.includes("dashboard"));
  assert.ok(args.includes("--tui"), "--tui is REQUIRED (else /api/ws closes 4403)");
  assert.ok(args.includes("--no-open"));
  assert.ok(args.includes("--skip-build"));
  // CRITICAL: no popup window.
  assert.equal(opts.windowsHide, true, "windowsHide MUST be true (no popup window)");
  assert.equal(opts.detached, true);

  assert.equal(out.port, 8765);
  assert.equal(out.token, "tok-abc123");
  assert.equal(out.wsUrl, "ws://127.0.0.1:8765/api/ws?token=tok-abc123");
});

test("ensureGatewayHost: waits for the dashboard to bind (retries on connection failure)", async () => {
  const { spawn, spawns } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch({ failTimes: 2 });

  const out = await ensureGatewayHost({
    agentId: "sc-hermes",
    port: 9001,
    spawn,
    fetchImpl,
    readyIntervalMs: 1,
  });
  assert.equal(out.token, "tok-abc123");
  assert.equal(spawns.length, 1);
});

test("ensureGatewayHost: idempotent — when the host already responds, does NOT spawn again", async () => {
  const { spawn, spawns } = makeFakeSpawn();
  // Probe succeeds immediately (host already up) → no spawn.
  const fetchImpl = makeFakeFetch({ failTimes: 0 });

  const out = await ensureGatewayHost({
    agentId: "sc-hermes",
    port: 9100,
    spawn,
    fetchImpl,
    probeFirst: true,
    readyIntervalMs: 1,
  });
  assert.equal(spawns.length, 0, "must NOT spawn when a host is already serving the index");
  assert.equal(out.token, "tok-abc123");
});

// ---------------------------------------------------------------------------
// teardown
// ---------------------------------------------------------------------------

test("teardownGatewayHost: kills the injected gateway-host child", async () => {
  let killed = false;
  const child = { kill: () => { killed = true; }, pid: 1234 };
  const state = {};
  await teardownGatewayHost({ child, state });
  assert.equal(killed, true, "must kill the gateway-host child");
});

test("teardownGatewayHost: guards against double teardown", async () => {
  let kills = 0;
  const child = { kill: () => { kills += 1; }, pid: 1 };
  const state = {};
  await teardownGatewayHost({ child, state });
  await teardownGatewayHost({ child, state });
  assert.equal(kills, 1, "double teardown kills only once");
});

test("installShutdownTeardown: SIGTERM handler tears down the gateway-host child then exits", async () => {
  const registered = {};
  const fakeProc = {
    once(sig, handler) {
      registered[sig] = handler;
    },
    exit(code) {
      this.exitCode = code;
    },
  };
  let killed = false;
  const child = { kill: () => { killed = true; }, pid: 7 };
  installShutdownTeardown({ getChild: () => child, proc: fakeProc, state: {} });

  assert.equal(typeof registered.SIGTERM, "function");
  assert.equal(typeof registered.SIGINT, "function");
  await registered.SIGTERM();
  assert.equal(killed, true, "SIGTERM must kill the gateway-host child");
});

// ---------------------------------------------------------------------------
// CLI dispatch — ensure-host + run
// ---------------------------------------------------------------------------

test("runEnsureHostCli: ensures the gateway host and prints ONE JSON line {port,token,wsUrl}", async () => {
  const { spawn, spawns } = makeFakeSpawn();
  // failTimes:1 → the idempotent probe misses once, so a host IS spawned.
  const fetchImpl = makeFakeFetch({ failTimes: 1 });
  let stdout = "";
  let stderr = "";

  const payload = await runEnsureHostCli("sc-hermes", {
    spawnImpl: spawn,
    fetchImpl,
    // Fake the python pre-seed so the test never touches the real SessionDB.
    spawnSyncImpl: () => ({ status: 0, stdout: "", stderr: "" }),
    out: (s) => (stdout += s),
    err: (s) => (stderr += s),
  });

  assert.equal(spawns.length, 1, "must spawn the hidden gateway host");
  // Exactly one JSON line to stdout.
  const lines = stdout.split("\n").filter(Boolean);
  assert.equal(lines.length, 1, "exactly one stdout line");
  const parsed = JSON.parse(lines[0]);
  assert.equal(parsed.token, "tok-abc123");
  assert.ok(typeof parsed.port === "number" && parsed.port >= 8642 && parsed.port <= 9641);
  assert.equal(parsed.wsUrl, `ws://127.0.0.1:${parsed.port}/api/ws?token=tok-abc123`);
  assert.deepEqual(payload, parsed);
  // resumeKey is the canonical pinnedSessionId == the key the delivery loop
  // matches in pickSessionForKey (`aify-<agentId>`).
  assert.equal(parsed.resumeKey, "aify-sc-hermes");
  // Loud-ish stderr breadcrumb, never on stdout.
  assert.ok(stderr.includes("sc-hermes"));
});

test("runEnsureHostCli: no agentId → throws (non-zero exit at the CLI boundary)", async () => {
  await assert.rejects(() => runEnsureHostCli("", { spawnImpl: makeFakeSpawn().spawn, fetchImpl: makeFakeFetch() }));
});

test("runDeliveryLoop: starts the claim/deliver loop and tears down the gateway host on release", async () => {
  const { spawn } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch();
  const { httpCall, calls } = makeAifyHttp({ release: true });
  const ws = makeFakeWsClient({ "session.active_list": ACTIVE_LIST_RESULT });
  let teardownChild;

  const result = await runDeliveryLoop("sc-hermes", {
    httpCall,
    spawnImpl: spawn,
    fetchImpl,
    openWs: async () => ws,
    // capture the child the loop wants torn down
    installTeardown: ({ getChild }) => {
      teardownChild = getChild;
    },
    sleepImpl: async () => {},
    serverUrl: "http://127.0.0.1:8800",
    maxIterations: 3,
  });

  assert.ok(result.released, "release signal stops the loop");
  // A claim was attempted against the gateway-backed agent.
  assert.ok(findCall(calls, "POST", "/dispatch/claim"));
  assert.equal(typeof teardownChild, "function", "teardown was wired to the gateway-host child");
});

test("runDeliveryLoop: delivers a claimed run via the gateway WS", async () => {
  const { spawn } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch();
  const { httpCall, calls } = makeAifyHttp({ claims: [SAMPLE_RUN] });
  const ws = makeFakeWsClient({
    "session.active_list": ACTIVE_LIST_RESULT,
    "prompt.submit": { status: "streaming" },
  });

  await runDeliveryLoop("sc-hermes", {
    httpCall,
    spawnImpl: spawn,
    fetchImpl,
    openWs: async () => ws,
    installTeardown: () => {},
    sleepImpl: async () => {},
    serverUrl: "http://127.0.0.1:8800",
    maxIterations: 1,
  });

  assert.ok(ws.sent.find((f) => f.method === "prompt.submit"), "delivered via prompt.submit");
  const patch = findCall(calls, "PATCH", (e) => e.startsWith("/dispatch/runs/"));
  assert.equal(String(patch.body.status), "delivered");
});

test("runCli: 'ensure-host <id>' routes to runEnsureHostCli and prints JSON", async () => {
  const { spawn } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch();
  let stdout = "";
  const res = await runCli(["ensure-host", "sc-hermes"], {
    spawnImpl: spawn,
    fetchImpl,
    spawnSyncImpl: () => ({ status: 0, stdout: "", stderr: "" }),
    out: (s) => (stdout += s),
    err: () => {},
  });
  assert.equal(res.mode, "ensure-host");
  assert.equal(res.agentId, "sc-hermes");
  const parsed = JSON.parse(stdout.trim());
  assert.equal(parsed.token, "tok-abc123");
});

test("runCli: 'run <id>' routes to the delivery loop", async () => {
  const { spawn } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch();
  const { httpCall, calls } = makeAifyHttp({ release: true });
  const ws = makeFakeWsClient({ "session.active_list": ACTIVE_LIST_RESULT });

  const res = await runCli(["run", "sc-hermes"], {
    httpCall,
    spawnImpl: spawn,
    fetchImpl,
    openWs: async () => ws,
    installTeardown: () => {},
    sleepImpl: async () => {},
    serverUrl: "http://127.0.0.1:8800",
    maxIterations: 2,
  });
  assert.equal(res.mode, "run");
  assert.equal(res.agentId, "sc-hermes");
  assert.ok(findCall(calls, "POST", "/dispatch/claim"));
});

// ---------------------------------------------------------------------------
// Stable-session pre-seed (BUG: --resume aify-<id> 4007 on first launch)
// ---------------------------------------------------------------------------

test("resolveHermesPython: finds the python sibling next to a path-style hermes", () => {
  // A bare command with no separators → PATH fallback (python3/python.exe).
  const fallback = resolveHermesPython("hermes");
  assert.ok(/python/.test(fallback));
});

test("ensureStableSession: runs python create-or-ignore with the canonical aify-<id> key", () => {
  const calls = [];
  const fakeSpawnSync = (cmd, args) => {
    calls.push({ cmd, args });
    return { status: 0, stdout: "", stderr: "" };
  };
  const ok = ensureStableSession({ agentId: "gov tui!", spawnSync: fakeSpawnSync });
  assert.equal(ok, true);
  assert.equal(calls.length, 1);
  // The session id arg MUST be the canonical pinnedSessionId (sanitized).
  const sessionArg = calls[0].args[calls[0].args.length - 1];
  assert.equal(sessionArg, "aify-gov-tui");
  // It must invoke an inline python program (-c) that imports SessionDB.
  assert.ok(calls[0].args.includes("-c"));
  assert.ok(calls[0].args.some((a) => /SessionDB/.test(a)));
});

test("ensureStableSession: empty agentId → no-op false", () => {
  let called = false;
  const ok = ensureStableSession({ agentId: "", spawnSync: () => { called = true; return { status: 0 }; } });
  assert.equal(ok, false);
  assert.equal(called, false);
});

test("ensureStableSession: python failure is swallowed (best-effort, returns false)", () => {
  const ok = ensureStableSession({
    agentId: "sc-hermes",
    spawnSync: () => ({ status: 2, stdout: "", stderr: "boom" }),
  });
  assert.equal(ok, false);
});

test("runEnsureHostCli: ensureSession:false skips the python pre-seed", async () => {
  const { spawn } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch();
  let preseedCalled = false;
  await runEnsureHostCli("sc-hermes", {
    spawnImpl: spawn,
    fetchImpl,
    ensureSession: false,
    spawnSyncImpl: () => { preseedCalled = true; return { status: 0 }; },
    out: () => {},
    err: () => {},
  });
  assert.equal(preseedCalled, false);
});
