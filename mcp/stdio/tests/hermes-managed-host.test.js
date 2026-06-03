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
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  ensureGatewayHost,
  deliverRun,
  waitForActiveSession,
  runPollCycle,
  teardownGatewayHost,
  installShutdownTeardown,
  runEnsureHostCli,
  runResolveSessionCli,
  runDeliveryLoop,
  runCli,
  ensureStableSession,
  resolveHermesPython,
  makeInFlightProbe,
  makeInFlightPulse,
  isGatewayConnectRefused,
  gatewayUnreachableMessage,
  noTuiAttachedMessage,
  reportGatewayDead,
  makeTeardown,
} from "../hermes-managed-host.js";
import { writeSessionIdMarker, readSessionIdMarker } from "../hermes-endpoint.js";

// Isolated temp dir for the native-session-id markers so tests never touch the
// shared process tmp dir (and so the real-id-match vs most-recent-fallback paths
// are deterministic). Threaded into deliverRun / runPollCycle / runDeliveryLoop
// via their `tempDir` / `markerDir` seams.
const MARKER_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "aify-hermes-host-test-"));
process.on("exit", () => {
  try {
    fs.rmSync(MARKER_DIR, { recursive: true, force: true });
  } catch {
    /* best-effort cleanup */
  }
});

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

// The agent's CURRENTLY-LIVE session. `started_at` is far-future so it always
// clears the stale-session freshness floor (FIX 1, 2026-06-03): waitForActiveSession's
// most-recent fallback only binds a row whose stamp is >= the delivery's start
// epoch (since), so a fixture standing in for "the fresh, just-attached session"
// must be dated at/after `since`. A fixed future date keeps the test
// wall-clock-independent.
const ACTIVE_LIST_RESULT = {
  result: {
    sessions: [
      { id: "live-sid-ab12", session_key: "aify-sc-hermes", status: "ready", started_at: "2099-01-01T00:00:00Z" },
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
    tempDir: MARKER_DIR,
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

test("deliverRun: on successful submit stamps inFlight {submittedAt, completed:false, runId} (opens re-pulse window) (#3)", async () => {
  const { httpCall } = makeAifyHttp();
  const ws = makeFakeWsClient({
    "session.active_list": ACTIVE_LIST_RESULT,
    "prompt.submit": { status: "streaming" },
  });
  const inFlight = { submittedAt: 0, completed: false, runId: "" };

  await deliverRun({
    run: SAMPLE_RUN,
    agentId: "sc-hermes",
    httpCall,
    wsClient: ws,
    tempDir: MARKER_DIR,
    inFlight,
    now: () => 123_456,
  });

  assert.equal(inFlight.submittedAt, 123_456, "window opened with the submit timestamp");
  assert.equal(inFlight.completed, false, "freshly-opened window is not completed");
  assert.equal(inFlight.runId, "run-1", "tracks WHICH run opened the window (for terminal-status polling)");
});

test("deliverRun: requeue (TUI never attaches) closes inFlight window {submittedAt:0, runId:''} (#3)", async () => {
  const { httpCall } = makeAifyHttp();
  const ws = makeFakeWsClient({
    "session.active_list": { result: { sessions: [] } }, // never attaches
    "prompt.submit": { status: "streaming" },
  });
  const inFlight = { submittedAt: 999, completed: false, runId: "old-run" };

  await deliverRun({
    run: SAMPLE_RUN,
    agentId: "sc-hermes",
    httpCall,
    wsClient: ws,
    tempDir: MARKER_DIR,
    inFlight,
    attachWaitMs: 10,
    attachPollMs: 1,
    sleepImpl: async () => {},
  });

  assert.equal(inFlight.submittedAt, 0, "requeue closes the window");
  assert.equal(inFlight.runId, "", "requeue clears the tracked runId");
});

test("deliverRun: busy 4009 on prompt.submit → falls back to session.steer", async () => {
  const { httpCall, calls } = makeAifyHttp();
  const busy = Object.assign(new Error("session busy"), { code: 4009 });
  const ws = makeFakeWsClient({
    "session.active_list": ACTIVE_LIST_RESULT,
    "prompt.submit": busy,
    "session.steer": { status: "queued" },
  });

  await deliverRun({ run: SAMPLE_RUN, agentId: "sc-hermes", httpCall, wsClient: ws, tempDir: MARKER_DIR });

  const steer = ws.sent.find((f) => f.method === "session.steer");
  assert.ok(steer, "expected a session.steer fallback frame on 4009 busy");
  assert.equal(steer.params.session_id, "live-sid-ab12", "steer targets the live runtime sid");

  // Still settled delivered.
  const patch = findCall(calls, "PATCH", (e) => e.startsWith("/dispatch/runs/"));
  assert.equal(String(patch.body.status), "delivered");
});

test("deliverRun: never caches the sid — re-runs active_list on every delivery", async () => {
  const { httpCall } = makeAifyHttp();
  // First delivery sees sid A; second sees a DIFFERENT ephemeral sid B. Both are
  // far-future-dated so each clears the stale-session freshness floor (FIX 1) —
  // these are the agent's fresh live sessions, re-discovered per delivery.
  const lists = [
    { result: { sessions: [{ id: "sid-A", session_key: "aify-sc-hermes", started_at: "2099-01-01T00:00:00Z" }] } },
    { result: { sessions: [{ id: "sid-B", session_key: "aify-sc-hermes", started_at: "2099-01-01T00:00:00Z" }] } },
  ];
  let i = 0;
  const ws = makeFakeWsClient({
    "session.active_list": () => lists[i++],
    "prompt.submit": { status: "streaming" },
  });

  await deliverRun({ run: SAMPLE_RUN, agentId: "sc-hermes", httpCall, wsClient: ws, tempDir: MARKER_DIR });
  await deliverRun({ run: { ...SAMPLE_RUN, id: "run-2" }, agentId: "sc-hermes", httpCall, wsClient: ws, tempDir: MARKER_DIR });

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
    tempDir: MARKER_DIR,
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
      tempDir: MARKER_DIR,
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

// ---------------------------------------------------------------------------
// BOUNDED NO-ATTACH FAIL (Task 2.3): after N consecutive empty-active_list
// requeues for the SAME run, the run is FAILED with an actionable "no visible TUI
// attached to gateway <url>" message instead of requeued forever (the ci-9136
// active_list=0 strand). A successful attach resets the per-run streak.
// ---------------------------------------------------------------------------

test("noTuiAttachedMessage: actionable — names the gateway URL, the empty-poll count, and says relaunch hermes-aify", () => {
  const msg = noTuiAttachedMessage("ws://127.0.0.1:9136/api/ws?token=x", 5);
  assert.match(msg, /no visible hermes tui attached/i);
  assert.match(msg, /ws:\/\/127\.0\.0\.1:9136/);
  assert.match(msg, /5 consecutive/i);
  assert.match(msg, /hermes-aify/i);
  assert.match(msg, /HERMES_TUI_GATEWAY_URL/);
});

test("deliverRun: below threshold → REQUEUE (cold start); the per-run empty-attach counter increments", async () => {
  const { httpCall, calls } = makeAifyHttp();
  const ws = makeFakeWsClient({ "session.active_list": { result: { sessions: [] } } });
  const emptyAttachCounter = new Map();

  await deliverRun({
    run: SAMPLE_RUN,
    agentId: "sc-hermes",
    httpCall,
    wsClient: ws,
    tempDir: MARKER_DIR,
    attachWaitMs: 5,
    attachPollMs: 1,
    sleepImpl: async () => {},
    emptyAttachCounter,
    emptyAttachFailThreshold: 3,
  });

  const patch = findCall(calls, "PATCH", (e) => e.startsWith("/dispatch/runs/"));
  assert.equal(String(patch.body.status), "queued", "first empty attach (below threshold) must requeue, not fail");
  assert.equal(emptyAttachCounter.get("run-1"), 1, "the per-run empty-attach streak must increment");
});

test("deliverRun: at threshold → FAIL with actionable no-TUI-attached message (mirrored to sender), NOT another requeue", async () => {
  const { httpCall, calls } = makeAifyHttp();
  const ws = makeFakeWsClient({ "session.active_list": { result: { sessions: [] } } });
  // Pre-seed the streak so this delivery is the Nth consecutive empty attach.
  const emptyAttachCounter = new Map([["run-1", 2]]);

  await deliverRun({
    run: SAMPLE_RUN,
    agentId: "sc-hermes",
    httpCall,
    wsClient: ws,
    tempDir: MARKER_DIR,
    attachWaitMs: 5,
    attachPollMs: 1,
    sleepImpl: async () => {},
    gatewayUrl: "ws://127.0.0.1:9136/api/ws?token=x",
    emptyAttachCounter,
    emptyAttachFailThreshold: 3,
  });

  const patch = findCall(calls, "PATCH", (e) => e.startsWith("/dispatch/runs/"));
  assert.equal(String(patch.body.status), "failed", "at the threshold the run must FAIL, not requeue forever");
  const detail = String(patch.body.error || patch.body.summary || "");
  assert.match(detail, /no visible hermes tui attached/i, "the failure must say no visible TUI attached");
  assert.match(detail, /ws:\/\/127\.0\.0\.1:9136/, "the failure must name the gateway URL");
  assert.match(detail, /hermes-aify/i, "the failure must tell the operator to relaunch hermes-aify");
  // No submit attempted (there was never a session), and the streak is cleared so
  // a later re-claim of a (different) run starts fresh.
  assert.ok(!ws.sent.find((f) => f.method === "prompt.submit"), "no submit when no session attached");
  assert.equal(emptyAttachCounter.has("run-1"), false, "the failed run's streak entry must be cleared");
  // The turn_busy pulse is cleared (the run is terminal, not 'working').
  assert.ok(findCall(calls, "POST", (e) => e.endsWith("/turn-end")), "a bounded-fail must clear the turn_busy pulse");
});

test("deliverRun: a successful attach RESETS the run's no-attach streak (slow cold start never penalized)", async () => {
  const { httpCall } = makeAifyHttp();
  const ws = makeFakeWsClient({
    "session.active_list": ACTIVE_LIST_RESULT,
    "prompt.submit": { status: "streaming" },
  });
  // The run had accumulated empties on prior cold polls; this delivery attaches.
  const emptyAttachCounter = new Map([["run-1", 4]]);

  await deliverRun({
    run: SAMPLE_RUN,
    agentId: "sc-hermes",
    httpCall,
    wsClient: ws,
    tempDir: MARKER_DIR,
    emptyAttachCounter,
    emptyAttachFailThreshold: 3,
  });

  assert.equal(emptyAttachCounter.has("run-1"), false, "a successful attach must clear the streak so it's never failed retroactively");
});

test("runPollCycle: threads the shared emptyAttachCounter into deliverRun (persists across cycles)", async () => {
  // One claim of an UNATTACHABLE run, threshold 1 → the single empty attach
  // immediately trips the bounded fail through the poll-cycle path.
  const { httpCall, calls } = makeAifyHttp({ claims: [SAMPLE_RUN] });
  const ws = makeFakeWsClient({ "session.active_list": { result: { sessions: [] } } });
  const emptyAttachCounter = new Map();

  // Tight attach timing via env so the poll-cycle deliverRun doesn't really wait.
  const prevWait = process.env.AIFY_HERMES_ATTACH_WAIT_MS;
  const prevPoll = process.env.AIFY_HERMES_ATTACH_POLL_MS;
  process.env.AIFY_HERMES_ATTACH_WAIT_MS = "5";
  process.env.AIFY_HERMES_ATTACH_POLL_MS = "1";
  try {
    await runPollCycle({
      agentId: "sc-hermes",
      httpCall,
      wsClient: ws,
      gatewayUrl: "ws://127.0.0.1:9136/api/ws?token=x",
      tempDir: MARKER_DIR,
      emptyAttachCounter,
      // The poll-cycle's deliverRun uses module ATTACH_* env (read at import), so we
      // also pass a small threshold via the counter pre-seed isn't possible here;
      // instead rely on the default threshold but assert the counter incremented,
      // proving the shared map is threaded through (persistence across cycles).
    });
  } finally {
    if (prevWait === undefined) delete process.env.AIFY_HERMES_ATTACH_WAIT_MS; else process.env.AIFY_HERMES_ATTACH_WAIT_MS = prevWait;
    if (prevPoll === undefined) delete process.env.AIFY_HERMES_ATTACH_POLL_MS; else process.env.AIFY_HERMES_ATTACH_POLL_MS = prevPoll;
  }

  // The shared counter saw this run (proves runPollCycle threads it to deliverRun).
  assert.equal(emptyAttachCounter.get("run-1"), 1, "runPollCycle must thread the shared emptyAttachCounter into deliverRun");
  // And below the default threshold it requeued (cold-start behaviour preserved).
  const patch = findCall(calls, "PATCH", (e) => e.startsWith("/dispatch/runs/"));
  assert.equal(String(patch.body.status), "queued");
});

// ---------------------------------------------------------------------------
// Gateway connect-refusal: reactive fail + self-correct (gateway-liveness gap).
//
// runtimes.js compute-capabilities grants resident-run to a hermes agent
// whenever runtimeConfig.gatewayUrl is a non-empty string (presence, not
// liveness). After the ephemeral gateway host on that port dies, the bridge
// keeps heartbeating and the agent shows `available` for the whole lease, so the
// dispatcher accepts a run that only discovers the dead port at connect time
// (ECONNREFUSED). These tests pin the REACTIVE mitigation: on an INITIAL gateway
// connect refusal, (a) fail the run with an actionable message and (b) signal
// the agent off `available` via the existing /agents/{id}/resident-lost path —
// while a NON-connect / mid-stream error does NOT trip the self-correct.
// ---------------------------------------------------------------------------

test("isGatewayConnectRefused: true for ECONNREFUSED (the dead-port incident shape)", () => {
  assert.equal(isGatewayConnectRefused(Object.assign(new Error("connect ECONNREFUSED 127.0.0.1:9342"), { code: "ECONNREFUSED" })), true);
  // Code-only (no descriptive message) still classifies.
  assert.equal(isGatewayConnectRefused({ code: "ECONNREFUSED" }), true);
});

test("isGatewayConnectRefused: true for other connect-unreachable codes (host/net/timeout/DNS)", () => {
  for (const code of ["EHOSTUNREACH", "ENETUNREACH", "ETIMEDOUT", "ENOTFOUND"]) {
    assert.equal(isGatewayConnectRefused({ code }), true, `expected ${code} to classify as gateway-unreachable`);
  }
});

test("isGatewayConnectRefused: FALSE for mid-stream / RPC / busy errors (no false self-correct)", () => {
  // These are the in-turn errors the WS client throws AFTER a healthy connect —
  // they must NOT be mistaken for a dead gateway port.
  assert.equal(isGatewayConnectRefused(new Error("hermes gateway WS closed")), false);
  assert.equal(isGatewayConnectRefused(new Error("hermes gateway WS not open")), false);
  assert.equal(isGatewayConnectRefused(new Error("hermes RPC prompt.submit timed out")), false);
  assert.equal(isGatewayConnectRefused(Object.assign(new Error("session busy"), { code: 4009 })), false);
  assert.equal(isGatewayConnectRefused(null), false);
  assert.equal(isGatewayConnectRefused(new Error("some unrelated failure")), false);
});

test("gatewayUnreachableMessage: actionable — names the URL, says refused, says restart hermes-aify", () => {
  const msg = gatewayUnreachableMessage("ws://127.0.0.1:9342/api/ws?token=x");
  assert.match(msg, /unreachable/i);
  assert.match(msg, /ws:\/\/127\.0\.0\.1:9342/);
  assert.match(msg, /restart/i);
  assert.match(msg, /hermes-aify/i);
});

test("reportGatewayDead: POSTs /agents/{id}/resident-lost WITHOUT a bridgeId (so the server guard doesn't ignore it)", async () => {
  const { httpCall, calls } = makeAifyHttp();
  await reportGatewayDead({
    httpCall,
    agentId: "sc-hermes",
    gatewayUrl: "ws://127.0.0.1:9342/api/ws",
    reason: "gateway refused",
  });
  const lost = findCall(calls, "POST", "/agents/sc-hermes/resident-lost");
  assert.ok(lost, "expected POST /agents/sc-hermes/resident-lost");
  assert.equal(lost.body.runtime, "hermes");
  assert.equal(lost.body.bridgeId ?? "", "", "must NOT send a bridgeId (channel-sidecar id != current resident bridge → would be ignored)");
  assert.match(String(lost.body.reason), /gateway refused/);
});

test("reportGatewayDead: swallows httpCall errors (best-effort self-correct)", async () => {
  const httpCall = async () => { throw new Error("network down"); };
  await assert.doesNotReject(() => reportGatewayDead({ httpCall, agentId: "sc-hermes", gatewayUrl: "ws://x" }));
});

test("deliverRun: gateway connect-refused mid-claim → FAILS run with actionable message AND self-corrects (resident-lost)", async () => {
  const { httpCall, calls } = makeAifyHttp();
  const econnrefused = Object.assign(new Error("connect ECONNREFUSED 127.0.0.1:9342"), { code: "ECONNREFUSED" });
  // active_list resolves the sid, then prompt.submit hits the dead gateway.
  const ws = makeFakeWsClient({
    "session.active_list": ACTIVE_LIST_RESULT,
    "prompt.submit": econnrefused,
  });

  await deliverRun({
    run: SAMPLE_RUN,
    agentId: "sc-hermes",
    httpCall,
    wsClient: ws,
    tempDir: MARKER_DIR,
    gatewayUrl: "ws://127.0.0.1:9342/api/ws",
  });

  // (a) run failed with the actionable message — not a raw ECONNREFUSED dump.
  const patch = findCall(calls, "PATCH", (e) => e.startsWith("/dispatch/runs/"));
  assert.ok(patch, "expected a PATCH /dispatch/runs/<id>");
  assert.equal(String(patch.body.status), "failed");
  assert.match(String(patch.body.error || patch.body.summary), /unreachable/i);
  assert.match(String(patch.body.error || patch.body.summary), /restart/i);
  // (b) self-corrected off `available` via resident-lost.
  assert.ok(findCall(calls, "POST", "/agents/sc-hermes/resident-lost"), "expected the resident-lost self-correct");
});

test("deliverRun: NON-connect submit error → FAILS run but does NOT self-correct (no false resident-lost)", async () => {
  const { httpCall, calls } = makeAifyHttp();
  // A mid-turn WS-closed style error (healthy connect, then the socket dropped).
  const ws = makeFakeWsClient({
    "session.active_list": ACTIVE_LIST_RESULT,
    "prompt.submit": new Error("hermes gateway WS closed"),
  });

  await deliverRun({
    run: SAMPLE_RUN,
    agentId: "sc-hermes",
    httpCall,
    wsClient: ws,
    tempDir: MARKER_DIR,
    gatewayUrl: "ws://127.0.0.1:9342/api/ws",
  });

  const patch = findCall(calls, "PATCH", (e) => e.startsWith("/dispatch/runs/"));
  assert.equal(String(patch.body.status), "failed", "non-connect error still fails the run");
  assert.ok(
    !findCall(calls, "POST", "/agents/sc-hermes/resident-lost"),
    "a mid-stream / non-connect error must NOT trip the gateway-dead self-correct",
  );
});

test("runDeliveryLoop: initial WS connect refused → self-corrects ONCE via resident-lost, does not spin the signal", async () => {
  const { spawn } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch();
  const { httpCall, calls } = makeAifyHttp();
  const econnrefused = Object.assign(new Error("connect ECONNREFUSED 127.0.0.1:9342"), { code: "ECONNREFUSED" });

  await runDeliveryLoop("sc-hermes", {
    httpCall,
    spawnImpl: spawn,
    fetchImpl,
    openWs: async () => { throw econnrefused; },
    installTeardown: () => {},
    sleepImpl: async () => {},
    serverUrl: "http://127.0.0.1:8800",
    maxIterations: 3,
  });

  const lostCalls = calls.filter((c) => c.method === "POST" && c.endpoint === "/agents/sc-hermes/resident-lost");
  assert.equal(lostCalls.length, 1, "gateway-dead self-correct must fire exactly once across repeated failed connects");
});

// ---------------------------------------------------------------------------
// waitForActiveSession — native-session-id resolution (2026-06-03 Task 3).
// PRIMARY: match the agent's bound REAL session id (from the marker) against an
// active_list row's real id. FALLBACK: when no id is bound OR the bound id isn't
// live yet, target the gateway's MOST-RECENT live session and persist that real
// id via the marker. Bounded-wait/poll behavior is preserved.
// ---------------------------------------------------------------------------

// A two-session active_list whose rows carry distinct REAL ids (NOT aify-<id>
// titles). real-2 is the freshest (latest started_at) → the most-recent fallback.
const TWO_REAL_SESSIONS = {
  result: {
    sessions: [
      { id: "real-1", status: "idle", started_at: "2026-06-03T10:00:00Z" },
      { id: "real-2", status: "working", started_at: "2026-06-03T11:00:00Z" },
    ],
  },
};

test("waitForActiveSession: PRIMARY — targets the agent's bound REAL session id (marker), not aify-<id>", async () => {
  const ws = makeFakeWsClient({ "session.active_list": TWO_REAL_SESSIONS });
  let id = 1;
  let wrote = null;
  const sid = await waitForActiveSession({
    wsClient: ws,
    agentId: "sc-hermes",
    // The bound real id is the OLDER session, not the most-recent — proves we
    // match by id, not by recency, when a marker exists.
    readMarker: () => "real-1",
    writeMarker: (a, v) => { wrote = { a, v }; },
    nextId: () => id++,
    deadlineMs: 1000,
    intervalMs: 1,
    sleepImpl: async () => {},
    log: () => {},
  });
  assert.equal(sid, "real-1", "must submit to the bound real session id");
  assert.equal(wrote, null, "no marker rewrite when the bound id already matches a live session");
});

test("waitForActiveSession: FALLBACK — no marker → most-recent live session, and PERSISTS its real id", async () => {
  const ws = makeFakeWsClient({ "session.active_list": TWO_REAL_SESSIONS });
  let id = 1;
  let wrote = null;
  const sid = await waitForActiveSession({
    wsClient: ws,
    agentId: "sc-hermes",
    readMarker: () => "", // no bound id yet
    writeMarker: (a, v, opts) => { wrote = { a, v, opts }; },
    tempDir: "/fake/tmp",
    nextId: () => id++,
    deadlineMs: 1000,
    intervalMs: 1,
    sleepImpl: async () => {},
    // Floor BEFORE the fixture's started_at so the fresh row clears it (FIX 1);
    // pinned so the test is wall-clock-independent.
    since: Date.parse("2026-06-03T00:00:00Z"),
    log: () => {},
  });
  assert.equal(sid, "real-2", "fallback targets the gateway's MOST-RECENT live session");
  assert.ok(wrote, "fallback must persist the resolved real id");
  assert.equal(wrote.a, "sc-hermes");
  assert.equal(wrote.v, "real-2", "persists the most-recent session's real id");
  assert.equal(wrote.opts?.tempDir, "/fake/tmp", "writes to the injected tempDir");
});

test("waitForActiveSession: FALLBACK — bound id present but NOT live → most-recent + re-persist", async () => {
  // The marker holds a stale real id no longer in active_list (e.g. the session
  // was forged fresh). Delivery must NOT hard-fail — fall back to most-recent and
  // re-bind so subsequent loops agree.
  const ws = makeFakeWsClient({ "session.active_list": TWO_REAL_SESSIONS });
  let id = 1;
  let wrote = null;
  const sid = await waitForActiveSession({
    wsClient: ws,
    agentId: "sc-hermes",
    readMarker: () => "real-GONE",
    writeMarker: (a, v) => { wrote = { a, v }; },
    nextId: () => id++,
    deadlineMs: 1000,
    intervalMs: 1,
    sleepImpl: async () => {},
    since: Date.parse("2026-06-03T00:00:00Z"), // floor below the fresh fixture (FIX 1)
    log: () => {},
  });
  assert.equal(sid, "real-2", "a stale bound id falls back to most-recent (never hard-fails)");
  assert.equal(wrote.v, "real-2", "re-persists the freshly-resolved real id");
});

test("waitForActiveSession: COLD START — empty then a real session appears → waits, then targets it", async () => {
  const lists = [{ result: { sessions: [] } }, { result: { sessions: [] } }, TWO_REAL_SESSIONS];
  let i = 0;
  const ws = makeFakeWsClient({
    "session.active_list": () => lists[Math.min(i++, lists.length - 1)],
  });
  let id = 1;
  const sid = await waitForActiveSession({
    wsClient: ws,
    agentId: "sc-hermes",
    readMarker: () => "real-1",
    writeMarker: () => {},
    nextId: () => id++,
    deadlineMs: 1000,
    intervalMs: 1,
    sleepImpl: async () => {},
    log: () => {},
  });
  assert.equal(sid, "real-1", "resolves once the bound real session attaches");
  assert.ok(i >= 3, "polled multiple times waiting for the cold-start attach");
});

test("waitForActiveSession: returns null after the deadline when NO session ever attaches (cold-start requeue)", async () => {
  const ws = makeFakeWsClient({ "session.active_list": { result: { sessions: [] } } });
  let id = 1;
  const sid = await waitForActiveSession({
    wsClient: ws,
    agentId: "sc-hermes",
    readMarker: () => "real-1",
    writeMarker: () => {},
    nextId: () => id++,
    deadlineMs: 5,
    intervalMs: 1,
    sleepImpl: async () => {},
    log: () => {},
  });
  assert.equal(sid, null);
});

test("waitForActiveSession: best-effort marker write — a throwing writeMarker NEVER breaks delivery", async () => {
  const ws = makeFakeWsClient({ "session.active_list": TWO_REAL_SESSIONS });
  let id = 1;
  const sid = await waitForActiveSession({
    wsClient: ws,
    agentId: "sc-hermes",
    readMarker: () => "",
    writeMarker: () => { throw new Error("disk full"); },
    nextId: () => id++,
    deadlineMs: 1000,
    intervalMs: 1,
    sleepImpl: async () => {},
    since: Date.parse("2026-06-03T00:00:00Z"), // floor below the fresh fixture (FIX 1)
    log: () => {},
  });
  assert.equal(sid, "real-2", "fallback still resolves even when the marker write throws");
});

test("waitForActiveSession: reads the bound real id from the marker (default readMarker) when wantId omitted", async () => {
  // Round-trip via the REAL marker file in the isolated MARKER_DIR: write the
  // agent's real id, then prove waitForActiveSession reads it and targets it.
  writeSessionIdMarker("marker-agent", "real-1", { tempDir: MARKER_DIR });
  assert.equal(readSessionIdMarker("marker-agent", { tempDir: MARKER_DIR }), "real-1");
  const ws = makeFakeWsClient({ "session.active_list": TWO_REAL_SESSIONS });
  let id = 1;
  const sid = await waitForActiveSession({
    wsClient: ws,
    agentId: "marker-agent",
    tempDir: MARKER_DIR,
    nextId: () => id++,
    deadlineMs: 1000,
    intervalMs: 1,
    sleepImpl: async () => {},
    log: () => {},
  });
  assert.equal(sid, "real-1", "default readMarker resolves the bound real id from the marker file");
});

// ---------------------------------------------------------------------------
// waitForActiveSession — STALE-SESSION BIND-RACE freshness floor (FIX 1, 2026-06-03).
// On a RELAUNCH the per-agent gateway host is REUSED, so the loop can poll
// session.active_list BEFORE the freshly-relaunched `hermes --tui` re-attaches.
// The most-recent FALLBACK must NOT bind a STALE prior session (started before
// this delivery attempt). Only a row whose freshness stamp is >= `since` is bound;
// a stale row keeps WAITING within the deadline. A marker-matched real id (PRIMARY)
// bypasses the floor (it's the intended session).
// ---------------------------------------------------------------------------

// One live session, STALE (started before the delivery attempt's `since`).
const STALE_ONLY_SESSIONS = {
  result: {
    sessions: [
      { id: "stale-real", status: "idle", started_at: "2026-06-03T09:00:00Z" },
    ],
  },
};

test("waitForActiveSession: FALLBACK freshness floor — DURING the relaunch grace a STALE most-recent row is NOT bound", async () => {
  // The only live row started at 09:00, but this delivery attempt's floor is 10:00
  // (a relaunch in progress). DURING the grace window the stale pre-attach session
  // must be SKIPPED — never bound, never persisted. We use a synthetic clock that
  // never reaches graceUntil within the deadline so we exercise the in-grace path:
  // the wait runs out → null (requeue), not a stale bind. (FIX A: the grace is
  // bounded; the "after grace" acceptance is covered by the next test.)
  const ws = makeFakeWsClient({ "session.active_list": STALE_ONLY_SESSIONS });
  let id = 1;
  let wrote = null;
  // Clock advances 1ms/call from the floor. With graceMs=1000 the grace never
  // elapses before the 10ms deadline, so we stay in the in-grace (skip-stale) path.
  let clock = Date.parse("2026-06-03T10:00:00Z");
  const sid = await waitForActiveSession({
    wsClient: ws,
    agentId: "sc-hermes",
    readMarker: () => "", // no bound id → fallback path
    writeMarker: (a, v) => { wrote = { a, v }; },
    nextId: () => id++,
    deadlineMs: 10,
    intervalMs: 1,
    sleepImpl: async () => {},
    since: Date.parse("2026-06-03T10:00:00Z"), // floor AFTER the stale row's start
    graceMs: 1000, // grace longer than the deadline → always in-grace
    now: () => {
      const t = clock;
      clock += 1;
      return t;
    },
    log: () => {},
  });
  assert.equal(sid, null, "a stale pre-attach session must NOT be bound during the relaunch grace");
  assert.equal(wrote, null, "a stale fallback row must NOT be persisted during grace");
});

test("waitForActiveSession: FALLBACK — AFTER the relaunch grace, an idle ATTACHED session (stale stamp) IS delivered to (FIX A regression)", async () => {
  // THE BUG: an idle attached session whose stamp predates delivery was skipped on
  // EVERY poll, so the run requeued forever and the operator only saw the placeholder.
  // Presence in active_list means it's live/attached → once the grace elapses it MUST
  // be bound + persisted, never requeued indefinitely.
  const ws = makeFakeWsClient({ "session.active_list": STALE_ONLY_SESSIONS });
  let id = 1;
  let wrote = null;
  // Clock starts at the floor and jumps past graceUntil quickly so the grace elapses.
  let clock = Date.parse("2026-06-03T10:00:00Z");
  const sid = await waitForActiveSession({
    wsClient: ws,
    agentId: "sc-hermes",
    readMarker: () => "", // no bound id → fallback path
    writeMarker: (a, v) => { wrote = { a, v }; },
    nextId: () => id++,
    deadlineMs: 100000, // generous: we want to prove it BINDS, not that it times out
    intervalMs: 1,
    sleepImpl: async () => {},
    since: Date.parse("2026-06-03T10:00:00Z"),
    graceMs: 50, // short grace
    now: () => {
      const t = clock;
      clock += 100; // each call jumps 100ms → grace (50ms) elapses by the 2nd poll
      return t;
    },
    log: () => {},
  });
  assert.equal(sid, "stale-real", "after grace, the idle attached session is bound despite its stale stamp");
  assert.ok(wrote && wrote.v === "stale-real", "the bound idle session is persisted to the marker");
});

test("waitForActiveSession: FALLBACK freshness floor — a FRESH most-recent row (>= since) IS bound + persisted", async () => {
  // Same shape, but now the row started at 11:00 which is >= the 10:00 floor — a
  // genuinely fresh post-relaunch attach → bind it and persist its real id.
  const FRESH = {
    result: { sessions: [{ id: "fresh-real", status: "working", started_at: "2026-06-03T11:00:00Z" }] },
  };
  const ws = makeFakeWsClient({ "session.active_list": FRESH });
  let id = 1;
  let wrote = null;
  const sid = await waitForActiveSession({
    wsClient: ws,
    agentId: "sc-hermes",
    readMarker: () => "",
    writeMarker: (a, v) => { wrote = { a, v }; },
    nextId: () => id++,
    deadlineMs: 1000,
    intervalMs: 1,
    sleepImpl: async () => {},
    since: Date.parse("2026-06-03T10:00:00Z"), // floor BEFORE the fresh row's start
    log: () => {},
  });
  assert.equal(sid, "fresh-real", "a fresh (>= since) most-recent row is bound");
  assert.ok(wrote && wrote.v === "fresh-real", "the fresh fallback row is persisted to the marker");
});

test("waitForActiveSession: freshness floor applies ONLY to the fallback — a marker-matched real id is bound even if STALE", async () => {
  // The bound real id (PRIMARY) is the agent's INTENDED session; it must be
  // delivered to regardless of its freshness stamp — the floor guards ONLY the
  // most-recent fallback (the relaunch bind-race), never the explicit binding.
  const ws = makeFakeWsClient({ "session.active_list": STALE_ONLY_SESSIONS });
  let id = 1;
  const sid = await waitForActiveSession({
    wsClient: ws,
    agentId: "sc-hermes",
    readMarker: () => "stale-real", // the marker points at the (stale-stamped) live row
    writeMarker: () => {},
    nextId: () => id++,
    deadlineMs: 10,
    intervalMs: 1,
    sleepImpl: async () => {},
    since: Date.parse("2026-06-03T10:00:00Z"), // floor AFTER the row's start — irrelevant to PRIMARY
    log: () => {},
  });
  assert.equal(sid, "stale-real", "a marker-matched real id bypasses the freshness floor (intended session)");
});

test("waitForActiveSession: freshness floor defaults to entry-time `now` when no `since` is passed (in-grace)", async () => {
  // With no explicit `since`, the floor is `now()` at ENTRY. A row dated BEFORE
  // that entry-now is stale → not bound DURING the grace window (null at deadline).
  // Proves the default wiring. `now` is captured ONCE for the floor but ADVANCES on
  // subsequent calls so the deadline check still fires. graceMs is pinned LONGER than
  // the deadline so we stay in the in-grace (skip-stale) path (FIX A: after-grace
  // acceptance is covered by the dedicated regression test above).
  const ws = makeFakeWsClient({ "session.active_list": STALE_ONLY_SESSIONS });
  let id = 1;
  // Entry-now sits AFTER the 2026-06-03T09:00 fixture so the default floor rejects
  // it; each later call advances 5ms so deadline (entry+10ms) is reached quickly.
  let clock = Date.parse("2026-06-03T10:00:00Z");
  const sid = await waitForActiveSession({
    wsClient: ws,
    agentId: "sc-hermes",
    readMarker: () => "",
    writeMarker: () => {},
    nextId: () => id++,
    deadlineMs: 10,
    intervalMs: 1,
    sleepImpl: async () => {},
    graceMs: 1000, // grace longer than the deadline → stay in the skip-stale path
    now: () => {
      const t = clock;
      clock += 5;
      return t;
    },
    log: () => {},
  });
  assert.equal(sid, null, "default since=now() rejects a row that started before entry (during grace)");
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
    tempDir: MARKER_DIR,
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

test("makeTeardown: does NOT kill a reused/shared gateway (child===null)", async () => {
  // 2026-06-02: a reused gateway (no owned child handle) is the wrapper/TUI's
  // gateway — shared with the visible TUI. The loop must NOT kill it (killing it
  // dropped the TUI's WebSocket). Only the loop's own markers are cleared.
  let cleared = false;
  const td = makeTeardown({ gatewayChild: null, clearMarkers: async () => { cleared = true; } });
  await td();
  assert.equal(cleared, true, "clears the loop's own markers");
  // No throw, no kill — there's nothing the loop owns to kill.
});

test("makeTeardown: kills the child ONLY when this loop spawned it", async () => {
  let childKilled = false;
  const td = makeTeardown({
    gatewayChild: { kill: () => { childKilled = true; } },
  });
  await td();
  assert.equal(childKilled, true, "must kill a gateway THIS loop spawned (owned child)");
});

test("makeTeardown: idempotent (double teardown clears once)", async () => {
  let clears = 0;
  const state = { done: false };
  const td = makeTeardown({ gatewayChild: null, clearMarkers: async () => { clears++; }, state });
  await td();
  await td();
  assert.equal(clears, 1, "teardown body runs at most once");
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
  // FIX 2 (2026-06-03): the legacy synthetic `resumeKey` (`aify-<id>`) is no
  // longer emitted — the native-session-id model resumes the agent's REAL session
  // id, so the dead field is dropped from the payload.
  assert.ok(!("resumeKey" in parsed), "resumeKey must NOT be emitted (dead synthetic resume key)");
  // Loud-ish stderr breadcrumb, never on stdout.
  assert.ok(stderr.includes("sc-hermes"));
});

test("runEnsureHostCli: no agentId → throws (non-zero exit at the CLI boundary)", async () => {
  await assert.rejects(() => runEnsureHostCli("", { spawnImpl: makeFakeSpawn().spawn, fetchImpl: makeFakeFetch() }));
});

// ---------------------------------------------------------------------------
// runResolveSessionCli — LAUNCH-SIDE session convergence (FIX C, 2026-06-03).
// Resolves the gateway's ground-truth session so the visible TUI resumes the SAME
// session the delivery loop will target: marker-if-live, else most-recent live,
// else empty. Persists the resolved id to the marker + seeds the active-session
// file so visible-TUI == loop == marker == active-session file.
// ---------------------------------------------------------------------------

test("runResolveSessionCli: marker id is LIVE in active_list → resumes it, no rewrite, seeds active file", async () => {
  const ws = makeFakeWsClient({ "session.active_list": TWO_REAL_SESSIONS });
  let stdout = "";
  let wroteMarker = null;
  let wroteActive = null;
  const res = await runResolveSessionCli("sc-hermes", {
    gatewayUrl: "ws://127.0.0.1:9000/api/ws?token=t",
    openClient: async () => ws,
    readMarker: () => "real-1", // bound id IS a live row
    writeMarker: (a, v) => { wroteMarker = { a, v }; },
    writeActiveSessionFile: (f, sid) => { wroteActive = { f, sid }; },
    activeSessionFile: "/fake/active.json",
    out: (s) => (stdout += s),
    err: () => {},
  });
  assert.equal(res.resolved, "real-1", "prefers the marker id when it is a live row");
  assert.equal(stdout.trim(), "real-1", "prints the resolved id on stdout");
  assert.equal(wroteMarker, null, "no marker rewrite when the marker already matches a live row");
  assert.deepEqual(wroteActive, { f: "/fake/active.json", sid: "real-1" }, "seeds the active-session file");
});

test("runResolveSessionCli: marker STALE (not live) → most-recent live session, persists marker + active file", async () => {
  const ws = makeFakeWsClient({ "session.active_list": TWO_REAL_SESSIONS });
  let stdout = "";
  let wroteMarker = null;
  let wroteActive = null;
  const res = await runResolveSessionCli("sc-hermes", {
    gatewayUrl: "ws://127.0.0.1:9000/api/ws?token=t",
    openClient: async () => ws,
    readMarker: () => "real-GONE", // stale marker, not in active_list
    writeMarker: (a, v) => { wroteMarker = { a, v }; },
    writeActiveSessionFile: (f, sid) => { wroteActive = { f, sid }; },
    activeSessionFile: "/fake/active.json",
    out: (s) => (stdout += s),
    err: () => {},
  });
  assert.equal(res.resolved, "real-2", "falls back to the gateway's most-recent live session");
  assert.equal(stdout.trim(), "real-2");
  assert.deepEqual(wroteMarker, { a: "sc-hermes", v: "real-2" }, "persists the resolved id to the marker");
  assert.deepEqual(wroteActive, { f: "/fake/active.json", sid: "real-2" }, "seeds the active-session file");
});

test("runResolveSessionCli: NO live session yet → empty result (wrapper resumes marker / starts fresh)", async () => {
  const ws = makeFakeWsClient({ "session.active_list": { result: { sessions: [] } } });
  let stdout = "";
  let wroteActive = null;
  const res = await runResolveSessionCli("sc-hermes", {
    gatewayUrl: "ws://127.0.0.1:9000/api/ws?token=t",
    openClient: async () => ws,
    readMarker: () => "", // no marker either
    writeMarker: () => {},
    writeActiveSessionFile: (f, sid) => { wroteActive = { f, sid }; },
    activeSessionFile: "/fake/active.json",
    out: (s) => (stdout += s),
    err: () => {},
  });
  assert.equal(res.resolved, "", "no live session → empty (never invents one)");
  assert.equal(stdout.trim(), "", "prints an empty line");
  assert.equal(wroteActive, null, "does not seed the active file when nothing resolved");
});

test("runResolveSessionCli: NO gateway url → falls back to the marker as-is (best known), never throws", async () => {
  let stdout = "";
  const res = await runResolveSessionCli("sc-hermes", {
    gatewayUrl: "", // no gateway to consult
    openClient: async () => { throw new Error("should not be called"); },
    readMarker: () => "real-marker",
    writeMarker: () => {},
    writeActiveSessionFile: () => {},
    activeSessionFile: "/fake/active.json",
    out: (s) => (stdout += s),
    err: () => {},
  });
  assert.equal(res.resolved, "real-marker", "no gateway → emit the marker as the best known id");
  assert.equal(stdout.trim(), "real-marker");
});

test("runResolveSessionCli: active_list query throws → falls back to the marker (never blocks launch)", async () => {
  let stdout = "";
  const res = await runResolveSessionCli("sc-hermes", {
    gatewayUrl: "ws://127.0.0.1:9000/api/ws?token=t",
    openClient: async () => { throw new Error("connect refused"); },
    readMarker: () => "real-marker",
    writeMarker: () => {},
    writeActiveSessionFile: () => {},
    activeSessionFile: "/fake/active.json",
    out: (s) => (stdout += s),
    err: () => {},
  });
  assert.equal(res.resolved, "real-marker", "query failure degrades to the marker, not a hard fail");
  assert.equal(stdout.trim(), "real-marker");
});

test("runResolveSessionCli: no agentId → throws (CLI boundary)", async () => {
  await assert.rejects(() => runResolveSessionCli("", {}));
});

// EXPLICIT-RESUME mode (BUG 2, 2026-06-03): `hermes-aify --resume <id>` makes <id>
// AUTHORITATIVE — resolve-session --explicit <id> SEEDS the active-session file +
// marker with <id>, SKIPS the gateway active_list query entirely, and prints <id>.
test("runResolveSessionCli --explicit: seeds marker + active file with the operator id, no gateway query", async () => {
  let stdout = "";
  let wroteMarker = null;
  let wroteActive = null;
  const res = await runResolveSessionCli("ci-senior-dev", {
    explicitId: "20260529_071302_ea65af",
    // Even WITH a gateway, the explicit short-circuit must not consult it.
    gatewayUrl: "ws://127.0.0.1:9000/api/ws?token=t",
    openClient: async () => { throw new Error("gateway must NOT be queried on explicit resume"); },
    readMarker: () => "20260603_034413_8480e3", // STALE marker (the live symptom)
    writeMarker: (a, v) => { wroteMarker = { a, v }; },
    writeActiveSessionFile: (f, sid) => { wroteActive = { f, sid }; },
    activeSessionFile: "/fake/active.json",
    out: (s) => (stdout += s),
    err: () => {},
  });
  assert.equal(res.resolved, "20260529_071302_ea65af", "explicit id is authoritative");
  assert.equal(res.source, "explicit-resume");
  assert.equal(stdout.trim(), "20260529_071302_ea65af", "prints the explicit id");
  assert.deepEqual(wroteMarker, { a: "ci-senior-dev", v: "20260529_071302_ea65af" }, "overwrites the stale marker with the explicit id");
  assert.deepEqual(wroteActive, { f: "/fake/active.json", sid: "20260529_071302_ea65af" }, "seeds the active-session file with the explicit id");
});

test("runResolveSessionCli --explicit: works even with NO gateway and NO active-file path (marker-only seed)", async () => {
  let stdout = "";
  let wroteMarker = null;
  let wroteActive = false;
  const res = await runResolveSessionCli("ci-senior-dev", {
    explicitId: "20260529_071302_ea65af",
    gatewayUrl: "",
    readMarker: () => "",
    writeMarker: (a, v) => { wroteMarker = { a, v }; },
    writeActiveSessionFile: () => { wroteActive = true; },
    activeSessionFile: "", // no active-file env → only the marker is seeded
    out: (s) => (stdout += s),
    err: () => {},
  });
  assert.equal(res.resolved, "20260529_071302_ea65af");
  assert.deepEqual(wroteMarker, { a: "ci-senior-dev", v: "20260529_071302_ea65af" });
  assert.equal(wroteActive, false, "no active-file path → does not attempt to seed it");
  assert.equal(stdout.trim(), "20260529_071302_ea65af");
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

// NO-TUI TEARDOWN BACKSTOP (FIX SET A2, 2026-06-03): a SIGKILL'd terminal bypasses
// the wrapper's A1 trap, so the loop must self-detect "no visible TUI attached"
// (session.active_list shows ZERO attached sessions) across a bounded number of
// poll cycles and tear itself down (resident-lost + reap the orphaned gateway).
const EMPTY_ACTIVE_LIST = { result: { sessions: [] } };

test("runDeliveryLoop: SUSTAINED empty active_list (no TUI attached) PAST the cold-start grace → resident-lost + teardown", async () => {
  const { spawn } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch();
  // claim returns { run: null } every cycle (claimOk, never terminal/release), so
  // the loop keeps polling — the no-TUI backstop is what must stop it.
  const { httpCall, calls } = makeAifyHttp();
  const ws = makeFakeWsClient({ "session.active_list": EMPTY_ACTIVE_LIST });
  let toreDown = false;
  // COLD-START GRACE (FIX, 2026-06-03): an empty active_list only counts toward
  // teardown once the TUI has been seen OR the grace elapsed. The TUI NEVER attaches
  // here (genuinely dead launch), so we drive the injected clock PAST the grace so the
  // empties start counting and the backstop still fires after the threshold.
  let clock = 0;
  const result = await runDeliveryLoop("sc-hermes", {
    httpCall,
    spawnImpl: spawn,
    fetchImpl,
    openWs: async () => ws,
    installTeardown: () => {},
    sleepImpl: async () => {},
    serverUrl: "http://127.0.0.1:8800",
    writeReady: () => {},
    clearReady: () => { toreDown = true; },
    killByPort: () => {},
    procExit: () => {},
    noTuiTeardownCycles: 3,
    noTuiGraceMs: 100,
    // First call (loopStartedAt) = 0; every later call jumps +1000ms so the grace
    // (100ms) is already elapsed by the first poll → empties count immediately.
    now: () => {
      const t = clock;
      clock += 1000;
      return t;
    },
    // More iterations than the override threshold so the backstop trips first.
    maxIterations: 20,
  });

  assert.equal(result.residentLost, true, "sustained no-TUI past the grace must flip the loop resident-lost");
  // The resident-lost self-correct was POSTed (reportGatewayDead → /resident-lost).
  const residentLost = calls.find(
    (c) => c.method === "POST" && c.endpoint === "/agents/sc-hermes/resident-lost",
  );
  assert.ok(residentLost, "expected a /resident-lost POST when no TUI stays attached");
  assert.ok(toreDown, "teardown (clearReady) must run so the orphaned gateway is reaped");
});

test("runDeliveryLoop: empty active_list DURING the cold-start grace then the TUI attaches → NO teardown (slow cold start)", async () => {
  // THE FIX: the delivery loop is spawned BEFORE the visible `hermes --tui` attaches.
  // A slow first-launch TUI build can leave active_list empty for many poll cycles;
  // the old code (no grace, no latch) tore the agent down right after launch. Here the
  // first K reads are empty (within the grace) and then the TUI attaches — the loop must
  // NOT have torn down.
  const { spawn } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch();
  const { httpCall, calls } = makeAifyHttp();
  let reads = 0;
  const ws = makeFakeWsClient({
    "session.active_list": () => {
      reads += 1;
      // The first several active_list reads are empty (TUI still cold-starting),
      // then it attaches for the rest of the loop.
      return reads <= 4 ? EMPTY_ACTIVE_LIST : ACTIVE_LIST_RESULT;
    },
  });

  // Clock stays WITHIN the grace for the empty cold-start window (so empties don't
  // count), then the TUI attaches before the grace elapses.
  let clock = 0;
  const result = await runDeliveryLoop("sc-hermes", {
    httpCall,
    spawnImpl: spawn,
    fetchImpl,
    openWs: async () => ws,
    installTeardown: () => {},
    sleepImpl: async () => {},
    serverUrl: "http://127.0.0.1:8800",
    writeReady: () => {},
    clearReady: () => {},
    killByPort: () => {},
    procExit: () => {},
    noTuiTeardownCycles: 3,
    noTuiGraceMs: 1_000_000, // generous grace: stays in-grace for the whole empty window
    now: () => {
      const t = clock;
      clock += 1; // tiny advance, never approaches the huge grace
      return t;
    },
    maxIterations: 8,
  });

  assert.notEqual(result.residentLost, true, "a slow cold-start (empty within grace, then attach) must NOT tear down");
  assert.ok(
    !calls.find((c) => c.method === "POST" && c.endpoint === "/agents/sc-hermes/resident-lost"),
    "no resident-lost POST when the TUI attaches within the cold-start grace",
  );
});

test("runDeliveryLoop: an attached session keeps the loop alive (no premature no-TUI teardown)", async () => {
  const { spawn } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch();
  const { httpCall, calls } = makeAifyHttp();
  // ACTIVE_LIST_RESULT carries one attached session on EVERY poll, so the no-TUI
  // counter must reset every cycle and never reach the threshold.
  const ws = makeFakeWsClient({ "session.active_list": ACTIVE_LIST_RESULT });

  const result = await runDeliveryLoop("sc-hermes", {
    httpCall,
    spawnImpl: spawn,
    fetchImpl,
    openWs: async () => ws,
    installTeardown: () => {},
    sleepImpl: async () => {},
    serverUrl: "http://127.0.0.1:8800",
    writeReady: () => {},
    clearReady: () => {},
    killByPort: () => {},
    procExit: () => {},
    noTuiTeardownCycles: 3,
    maxIterations: 10,
  });

  assert.notEqual(result.residentLost, true, "an attached session must NOT trigger no-TUI teardown");
  assert.ok(
    !calls.find((c) => c.method === "POST" && c.endpoint === "/agents/sc-hermes/resident-lost"),
    "no resident-lost POST while a session stays attached",
  );
});

test("runDeliveryLoop: a SINGLE empty active_list does not tear down (brief relaunch gap tolerated)", async () => {
  const { spawn } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch();
  const { httpCall, calls } = makeAifyHttp();
  // Only the VERY FIRST active_list read is empty (TUI mid-relaunch); every read
  // after is attached. The per-cycle counter sees at most one empty then resets,
  // so it can never reach the threshold (3). Keyed on call ordinal (not per-cycle)
  // so it's robust to however many active_list reads each cycle performs.
  let firstRead = true;
  const ws = makeFakeWsClient({
    "session.active_list": () => {
      if (firstRead) {
        firstRead = false;
        return EMPTY_ACTIVE_LIST;
      }
      return ACTIVE_LIST_RESULT;
    },
  });

  const result = await runDeliveryLoop("sc-hermes", {
    httpCall,
    spawnImpl: spawn,
    fetchImpl,
    openWs: async () => ws,
    installTeardown: () => {},
    sleepImpl: async () => {},
    serverUrl: "http://127.0.0.1:8800",
    writeReady: () => {},
    clearReady: () => {},
    killByPort: () => {},
    procExit: () => {},
    noTuiTeardownCycles: 3,
    maxIterations: 6,
  });

  assert.notEqual(result.residentLost, true, "a single empty poll must not trip the no-TUI backstop");
  assert.ok(
    !calls.find((c) => c.method === "POST" && c.endpoint === "/agents/sc-hermes/resident-lost"),
    "no resident-lost POST after a single empty poll",
  );
});

test("runDeliveryLoop: writes the loop-ready marker after the first successful claim round-trip (Task 1.4)", async () => {
  const { spawn } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch();
  const { httpCall } = makeAifyHttp(); // claim returns { run: null } => claimOk, 0 runs
  const ws = makeFakeWsClient({ "session.active_list": ACTIVE_LIST_RESULT });
  const written = [];

  await runDeliveryLoop("sc-hermes", {
    httpCall,
    spawnImpl: spawn,
    fetchImpl,
    openWs: async () => ws,
    installTeardown: () => {},
    sleepImpl: async () => {},
    serverUrl: "http://127.0.0.1:8800",
    writeReady: (id) => written.push(id),
    clearReady: () => {},
    maxIterations: 1,
  });

  assert.ok(written.includes("sc-hermes"), "ready marker written after a successful claim round-trip");
});

test("runDeliveryLoop: clears the loop-ready marker on terminal teardown (Task 1.4)", async () => {
  const { spawn } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch();
  const httpCall = async (method, endpoint) => {
    if (method === "POST" && endpoint === "/dispatch/claim") {
      const e = new Error("HTTP 410: gone");
      e.status = 410;
      throw e;
    }
    return { ok: true };
  };
  const ws = makeFakeWsClient({ "session.active_list": ACTIVE_LIST_RESULT });
  const cleared = [];

  await runDeliveryLoop("sc-hermes", {
    httpCall,
    spawnImpl: spawn,
    fetchImpl,
    openWs: async () => ws,
    installTeardown: () => {},
    sleepImpl: async () => {},
    serverUrl: "http://127.0.0.1:8800",
    writeReady: () => {},
    clearReady: (id) => cleared.push(id),
    killByPort: () => {},
    procExit: () => {},
    maxIterations: 5,
  });

  assert.ok(cleared.includes("sc-hermes"), "ready marker cleared on terminal teardown");
});

test("runDeliveryLoop: POSTs claimer-acquire after the first successful claim round-trip (WS5 Task 5.1)", async () => {
  const { spawn } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch();
  const { httpCall, calls } = makeAifyHttp(); // claim returns { run: null } => claimOk
  const ws = makeFakeWsClient({ "session.active_list": ACTIVE_LIST_RESULT });

  await runDeliveryLoop("sc-hermes", {
    httpCall,
    spawnImpl: spawn,
    fetchImpl,
    openWs: async () => ws,
    installTeardown: () => {},
    sleepImpl: async () => {},
    serverUrl: "http://127.0.0.1:8800",
    writeReady: () => {},
    clearReady: () => {},
    maxIterations: 1,
  });

  const acquire = findCall(calls, "POST", "/agents/sc-hermes/claimer-lease");
  assert.ok(acquire, "expected a claimer-lease POST after becoming a live claimer");
  assert.equal(acquire.body.action, "acquire", "must POST action=acquire on ready");
});

test("runDeliveryLoop: POSTs claimer-release on terminal teardown (WS5 Task 5.1)", async () => {
  const { spawn } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch();
  const calls = [];
  const httpCall = async (method, endpoint, body = null) => {
    calls.push({ method, endpoint, body });
    if (method === "POST" && endpoint === "/dispatch/claim") {
      const e = new Error("HTTP 410: gone");
      e.status = 410;
      throw e;
    }
    return { ok: true };
  };
  const ws = makeFakeWsClient({ "session.active_list": ACTIVE_LIST_RESULT });

  await runDeliveryLoop("sc-hermes", {
    httpCall,
    spawnImpl: spawn,
    fetchImpl,
    openWs: async () => ws,
    installTeardown: () => {},
    sleepImpl: async () => {},
    serverUrl: "http://127.0.0.1:8800",
    writeReady: () => {},
    clearReady: () => {},
    killByPort: () => {},
    procExit: () => {},
    maxIterations: 5,
  });

  const release = calls.find(
    (c) => c.method === "POST" && c.endpoint === "/agents/sc-hermes/claimer-lease" && c.body?.action === "release",
  );
  assert.ok(release, "expected a claimer-lease release POST on terminal teardown");
});

test("runDeliveryLoop: clears gateway port/key markers on agent-removed (410) terminal teardown (fix/hermes-leak P4)", async () => {
  // On a TERMINAL agent-removed (410) teardown the agent is tombstoned, so there
  // will be NO relaunch — the port/key markers kept for kill-prior are now dead
  // weight. The loop must clear them so the env-bridge boot sweep doesn't keep
  // re-finding a phantom gateway for a removed agent.
  const { spawn } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch();
  const httpCall = async (method, endpoint) => {
    if (method === "POST" && endpoint === "/dispatch/claim") {
      const e = new Error("HTTP 410: gone");
      e.status = 410;
      throw e;
    }
    return { ok: true };
  };
  const ws = makeFakeWsClient({ "session.active_list": ACTIVE_LIST_RESULT });
  const clearedGateway = [];
  const clearedSession = [];

  await runDeliveryLoop("sc-hermes", {
    httpCall,
    spawnImpl: spawn,
    fetchImpl,
    openWs: async () => ws,
    installTeardown: () => {},
    sleepImpl: async () => {},
    serverUrl: "http://127.0.0.1:8800",
    writeReady: () => {},
    clearReady: () => {},
    clearGatewayMarkers: (id) => clearedGateway.push(id),
    clearSessionMarker: (id) => clearedSession.push(id),
    killByPort: () => {},
    procExit: () => {},
    maxIterations: 5,
  });

  assert.ok(
    clearedGateway.includes("sc-hermes"),
    "agent-removed (410) teardown must clear the gateway port/key markers (no relaunch coming)",
  );
  // FIX 3 (2026-06-03): a TERMINAL agent-removal must ALSO clear the persistent
  // session-id marker so the deleted agent leaves no stale agent→session binding.
  assert.ok(
    clearedSession.includes("sc-hermes"),
    "agent-removed (410) teardown must clear the session-id marker (terminal cleanup)",
  );
});

test("runDeliveryLoop: does NOT clear gateway markers on a non-removed (released) teardown (fix/hermes-leak P4)", async () => {
  // A `released` (graceful, non-410) teardown is NOT an agent removal — the
  // agent may relaunch — so the gateway port/key markers MUST be preserved for
  // kill-prior (the 2026-06-02 port-drift guard). Only agent-removed clears them.
  const { spawn } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch();
  let claims = 0;
  const httpCall = async (method, endpoint) => {
    if (method === "POST" && endpoint === "/dispatch/claim") {
      claims += 1;
      // Signal a graceful release (not a 410) on the first claim.
      return { release: true };
    }
    return { ok: true };
  };
  const ws = makeFakeWsClient({ "session.active_list": ACTIVE_LIST_RESULT });
  const clearedGateway = [];
  const clearedSession = [];

  await runDeliveryLoop("sc-hermes", {
    httpCall,
    spawnImpl: spawn,
    fetchImpl,
    openWs: async () => ws,
    installTeardown: () => {},
    sleepImpl: async () => {},
    serverUrl: "http://127.0.0.1:8800",
    writeReady: () => {},
    clearReady: () => {},
    clearGatewayMarkers: (id) => clearedGateway.push(id),
    clearSessionMarker: (id) => clearedSession.push(id),
    killByPort: () => {},
    procExit: () => {},
    maxIterations: 3,
  });

  assert.equal(
    clearedGateway.length,
    0,
    "a non-removed (released) teardown must preserve gateway markers for kill-prior",
  );
  // FIX 3 (2026-06-03): a relaunch-capable (released) teardown must PRESERVE the
  // session-id marker so the next launch resumes the SAME transcript — only a
  // TERMINAL agent-removal clears it.
  assert.equal(
    clearedSession.length,
    0,
    "a released (relaunch-capable) teardown must NOT clear the session-id marker",
  );
});

test("runDeliveryLoop: 410 from /dispatch/claim self-exits(0) WITHOUT killing the shared gateway, does not keep polling", async () => {
  const { spawn } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch();
  // claim always 410 (agent tombstoned).
  const calls = [];
  const httpCall = async (method, endpoint, body = null) => {
    calls.push({ method, endpoint, body });
    if (method === "POST" && endpoint === "/dispatch/claim") {
      const e = new Error("HTTP 410: gone");
      e.status = 410;
      throw e;
    }
    return { ok: true };
  };
  const ws = makeFakeWsClient({ "session.active_list": ACTIVE_LIST_RESULT });
  let exitCode;
  const killedPorts = [];

  const result = await runDeliveryLoop("sc-hermes", {
    httpCall,
    spawnImpl: spawn,
    fetchImpl,
    openWs: async () => ws,
    installTeardown: () => {},
    sleepImpl: async () => {},
    serverUrl: "http://127.0.0.1:8800",
    procExit: (code) => {
      exitCode = code;
    },
    killByPort: (p) => {
      killedPorts.push(p);
    },
    // 404 grace not relevant; 410 is immediate-terminal.
    maxIterations: 5,
  });

  assert.equal(exitCode, 0, "terminal 410 must self-exit(0)");
  assert.equal(result.terminal, "agent-removed", "loop reports the terminal reason");
  // 2026-06-02: the reused gateway host (child===null via probeFirst) is SHARED
  // with the visible TUI, so the loop must NOT port-kill it on teardown (doing so
  // dropped the TUI's WebSocket). It self-exits and leaves the gateway for the
  // TUI; kill-prior / the env-bridge sweep reap it later by its port marker.
  assert.equal(killedPorts.length, 0, "must NOT port-kill the shared/reused gateway");
  // It did NOT keep polling indefinitely (broke on the first terminal claim).
  const claimCount = calls.filter((c) => c.endpoint === "/dispatch/claim").length;
  assert.ok(claimCount <= 2, `must stop claiming after terminal 410 (got ${claimCount})`);
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
    markerDir: MARKER_DIR,
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
// makeInFlightProbe — the re-pulse gate (#3 false-working fix + #172 safety)
// ---------------------------------------------------------------------------

const WIN = 15 * 60 * 1000;

test("makeInFlightProbe: open window + delivered require_reply=1 → keeps re-pulsing (no #172 regression)", async () => {
  // A long turn still 'delivered' with require_reply=1 (a real turn the agent is
  // working before it self-replies) must keep the beat alive so a >120s turn
  // keeps showing `working`.
  const inFlight = { submittedAt: Date.now() - 5 * 60 * 1000, completed: false, runId: "run-1" };
  const probe = makeInFlightProbe({
    inFlight,
    serverUrl: "http://x",
    httpCall: async () => ({ run: { status: "delivered", requireReply: true } }),
    maxWindowMs: WIN,
  });
  assert.equal(await probe(), true, "delivered + rr=1 within window must keep re-pulsing");
  assert.equal(inFlight.completed, false, "must NOT latch on a delivered require_reply=1 run");
});

test("makeInFlightProbe: open window + delivered require_reply=0 → latches and STOPS (false-busy fix)", async () => {
  // The 2026-06-02 bug: a delivery-only run (info/nudge, require_reply=0) lingers
  // 'delivered' (reconcile only closes it after 24h), so the old terminal-only
  // latch re-pulsed turn_busy forever → agent stuck `working` → queued messages
  // blocked + contract reminders skipped. A delivered require_reply=0 run owes no
  // tracked turn, so it must latch immediately.
  const inFlight = { submittedAt: Date.now() - 5 * 60 * 1000, completed: false, runId: "run-1" };
  let polls = 0;
  const probe = makeInFlightProbe({
    inFlight,
    serverUrl: "http://x",
    httpCall: async () => {
      polls++;
      return { run: { status: "delivered", requireReply: false } };
    },
    maxWindowMs: WIN,
  });
  assert.equal(await probe(), false, "delivered + rr=0 must stop the beat (no false-busy)");
  assert.equal(inFlight.completed, true, "delivered + rr=0 latches completion");
  assert.equal(inFlight.runId, "", "tracked runId cleared on latch");
  assert.equal(await probe(), false, "stays latched");
  assert.equal(polls, 1, "must not keep polling after the false-busy latch");
});

test("makeInFlightProbe: terminal run status latches completed=true and STOPS the beat (#3)", async () => {
  // The bug: a turn that finishes (agent self-replied → run 'completed') kept
  // re-pulsing turn_busy=true until the 15-min cap. Observing the terminal
  // status must stop the beat immediately.
  const inFlight = { submittedAt: Date.now() - 2 * 60 * 1000, completed: false, runId: "run-1" };
  let polls = 0;
  const probe = makeInFlightProbe({
    inFlight,
    serverUrl: "http://x",
    httpCall: async () => {
      polls++;
      return { run: { status: "completed" } };
    },
    maxWindowMs: WIN,
  });
  assert.equal(await probe(), false, "completed run must stop the beat well within the window");
  assert.equal(inFlight.completed, true, "completion is latched");
  assert.equal(inFlight.runId, "", "tracked runId cleared on completion");
  // Latched: a second call short-circuits via shouldManagedHostRepulse(completed)
  // and does NOT poll again.
  assert.equal(await probe(), false);
  assert.equal(polls, 1, "must not keep polling run status after completion latches");
});

test("makeInFlightProbe: failed/cancelled/stopped also stop the beat", async () => {
  for (const status of ["failed", "cancelled", "stopped"]) {
    const inFlight = { submittedAt: Date.now() - 1000, completed: false, runId: "run-x" };
    const probe = makeInFlightProbe({
      inFlight,
      serverUrl: "http://x",
      httpCall: async () => ({ run: { status } }),
      maxWindowMs: WIN,
    });
    assert.equal(await probe(), false, `terminal '${status}' must stop the beat`);
    assert.equal(inFlight.completed, true);
  }
});

test("makeInFlightProbe: past the hard window → false WITHOUT polling run status (15-min cap backstop)", async () => {
  let polls = 0;
  const inFlight = { submittedAt: Date.now() - (WIN + 1000), completed: false, runId: "run-1" };
  const probe = makeInFlightProbe({
    inFlight,
    serverUrl: "http://x",
    httpCall: async () => {
      polls++;
      return { run: { status: "delivered" } };
    },
    maxWindowMs: WIN,
  });
  assert.equal(await probe(), false, "expired window stops the beat regardless of run status");
  assert.equal(polls, 0, "expired window short-circuits before any run-status poll");
});

test("makeInFlightProbe: no submit (closed window) → false, no poll", async () => {
  let polls = 0;
  const inFlight = { submittedAt: 0, completed: false, runId: "" };
  const probe = makeInFlightProbe({
    inFlight,
    serverUrl: "http://x",
    httpCall: async () => {
      polls++;
      return {};
    },
    maxWindowMs: WIN,
  });
  assert.equal(await probe(), false);
  assert.equal(polls, 0, "no open window → never polls");
});

test("makeInFlightProbe: run-status fetch error → treated as non-terminal, keeps re-pulsing (best-effort)", async () => {
  const inFlight = { submittedAt: Date.now() - 1000, completed: false, runId: "run-1" };
  const probe = makeInFlightProbe({
    inFlight,
    serverUrl: "http://x",
    httpCall: async () => {
      throw new Error("network down");
    },
    maxWindowMs: WIN,
  });
  // A transient status-read failure must NOT prematurely latch completion (that
  // would under-show working). It stays in-flight until window expiry.
  assert.equal(await probe(), true, "status read failure → assume still in-flight (no premature stop)");
  assert.equal(inFlight.completed, false);
});

// ---------------------------------------------------------------------------
// WS5 Task 5.2 — event-driven turn-END via the gateway session status.
// The probe observes session.active_list `status` (the gateway's own
// session["running"] truth). When it reads "idle" AFTER having seen "working"
// (a real turn boundary), it latches completion AND fires the authoritative
// /turn-end (clearTurnImpl) so turn_busy clears IMMEDIATELY — no 120s wait.
// ---------------------------------------------------------------------------

test("makeInFlightProbe: gateway idle SUSTAINED (>= debounce) after working → latches + fires /turn-end once (Bug A, debounced)", async () => {
  // turn-END signal: the gateway reports the aify-<agent> session 'working' (mid-
  // turn), then SUSTAINED 'idle' (turn ended). With the debounce (idleDebounce=2),
  // the turn-end latches only on the 2nd consecutive idle — and then the probe
  // stops the beat AND clears turn_busy authoritatively (the 120s stale window is
  // now a backstop, not the primary transition).
  const inFlight = { submittedAt: Date.now() - 60 * 1000, completed: false, runId: "run-1" };
  const statuses = ["working", "idle", "idle"];
  let i = 0;
  let cleared = 0;
  const probe = makeInFlightProbe({
    inFlight,
    serverUrl: "http://x",
    httpCall: async () => ({ run: { status: "delivered", requireReply: true } }),
    readGatewayStatus: async () => statuses[Math.min(i++, statuses.length - 1)],
    clearTurnImpl: async () => {
      cleared++;
    },
    maxWindowMs: WIN,
    idleDebounce: 2,
  });
  assert.equal(await probe(), true, "first tick: gateway 'working' → keep re-pulsing (mid-turn)");
  assert.equal(cleared, 0, "no turn-end while working");
  assert.equal(await probe(), true, "second tick: first 'idle' below debounce → still re-pulsing (no false clear)");
  assert.equal(cleared, 0, "one idle must NOT clear");
  assert.equal(await probe(), false, "third tick: second consecutive 'idle' → turn ended, stop the beat");
  assert.equal(inFlight.completed, true, "sustained idle latches completion");
  assert.equal(cleared, 1, "sustained idle fires the authoritative /turn-end exactly once");
  // Latched: a fourth call short-circuits and does NOT clear again.
  assert.equal(await probe(), false);
  assert.equal(cleared, 1, "turn-end fires once, not on every subsequent tick");
});

test("makeInFlightProbe: momentary mid-turn idle (working→idle→working) does NOT clear (the flap fix)", async () => {
  // THE REPORTED FLAP: the gateway session['running'] flips False for a tick mid-
  // turn (between tool calls / a generation gap), surfacing 'idle', then back to
  // 'working'. A single-idle latch false-cleared turn_busy → working↔online flap.
  // With the debounce, the lone idle increments the streak but the next 'working'
  // resets it, so the turn is NEVER ended early.
  const inFlight = { submittedAt: Date.now() - 60 * 1000, completed: false, runId: "run-1" };
  const statuses = ["working", "idle", "working", "idle", "working"];
  let i = 0;
  let cleared = 0;
  const probe = makeInFlightProbe({
    inFlight,
    serverUrl: "http://x",
    httpCall: async () => ({ run: { status: "delivered", requireReply: true } }),
    readGatewayStatus: async () => statuses[Math.min(i++, statuses.length - 1)],
    clearTurnImpl: async () => {
      cleared++;
    },
    maxWindowMs: WIN,
    idleDebounce: 3,
  });
  for (let t = 0; t < statuses.length; t++) {
    assert.equal(await probe(), true, `tick ${t}: mid-turn blip must keep re-pulsing`);
  }
  assert.equal(cleared, 0, "no flap: a momentary idle between working reads never clears");
  assert.equal(inFlight.completed, false, "turn stays in-flight across mid-turn idle blips");
});

test("makeInFlightProbe: a transient 'idle' BEFORE any 'working' does NOT end the turn (submit race guard)", async () => {
  // prompt.submit returns {streaming} immediately; the gateway flips running=True
  // in a worker thread a beat later. A probe that catches the session momentarily
  // 'idle' BEFORE the turn thread starts must NOT treat it as turn-end (that would
  // under-show working — the #172 trap). Only idle-AFTER-working is a real end.
  const inFlight = { submittedAt: Date.now() - 1000, completed: false, runId: "run-1" };
  let cleared = 0;
  const probe = makeInFlightProbe({
    inFlight,
    serverUrl: "http://x",
    httpCall: async () => ({ run: { status: "delivered", requireReply: true } }),
    readGatewayStatus: async () => "idle", // never observed working yet
    clearTurnImpl: async () => {
      cleared++;
    },
    maxWindowMs: WIN,
  });
  assert.equal(await probe(), true, "idle before any working → assume turn not started yet → keep re-pulsing");
  assert.equal(inFlight.completed, false, "no premature completion");
  assert.equal(cleared, 0, "no premature /turn-end");
});

test("makeInFlightProbe: gateway status read error → no false turn-end (falls back to run-status path)", async () => {
  // A gateway hiccup must NOT clear turn_busy. The probe falls through to the
  // existing run-status latch (still in-flight here) and keeps re-pulsing.
  const inFlight = { submittedAt: Date.now() - 60 * 1000, completed: false, runId: "run-1" };
  let cleared = 0;
  const probe = makeInFlightProbe({
    inFlight,
    serverUrl: "http://x",
    httpCall: async () => ({ run: { status: "delivered", requireReply: true } }),
    readGatewayStatus: async () => {
      throw new Error("gateway WS hiccup");
    },
    clearTurnImpl: async () => {
      cleared++;
    },
    maxWindowMs: WIN,
  });
  assert.equal(await probe(), true, "gateway read failure → not idle → keep re-pulsing");
  assert.equal(inFlight.completed, false);
  assert.equal(cleared, 0, "no /turn-end on a gateway read failure");
});

test("makeInFlightProbe: backward-compatible — no readGatewayStatus uses the run-status latch only", async () => {
  // The gateway-status reader is optional; without it the probe behaves exactly as
  // before (run-status terminal latch), so existing callers/tests are unaffected.
  const inFlight = { submittedAt: Date.now() - 1000, completed: false, runId: "run-1" };
  const probe = makeInFlightProbe({
    inFlight,
    serverUrl: "http://x",
    httpCall: async () => ({ run: { status: "completed" } }),
    maxWindowMs: WIN,
  });
  assert.equal(await probe(), false, "run-status terminal latch still works with no gateway reader");
  assert.equal(inFlight.completed, true);
});

test("makeInFlightProbe: no serverUrl → false (never beats offline)", async () => {
  const inFlight = { submittedAt: Date.now(), completed: false, runId: "run-1" };
  const probe = makeInFlightProbe({ inFlight, serverUrl: "", httpCall: async () => ({}), maxWindowMs: WIN });
  assert.equal(await probe(), false);
});

// ---------------------------------------------------------------------------
// makeInFlightPulse — the re-pulse must thread the OPEN run's id (linkage fix)
// ---------------------------------------------------------------------------

test("makeInFlightPulse: re-pulse posts busy=true WITH the in-flight runId (preserves turn_run_id linkage)", async () => {
  const posts = [];
  const inFlight = { submittedAt: Date.now(), completed: false, runId: "run-77" };
  const pulse = makeInFlightPulse({
    httpCall: async () => ({ ok: true }),
    agentId: "sc-hermes",
    inFlight,
    reportTurnBusyImpl: async (_httpCall, agentId, body) => {
      posts.push({ agentId, body });
    },
  });

  await pulse();

  assert.equal(posts.length, 1, "exactly one re-pulse");
  assert.equal(posts[0].agentId, "sc-hermes");
  assert.equal(posts[0].body.busy, true, "re-pulse marks busy");
  assert.equal(
    posts[0].body.runId,
    "run-77",
    "re-pulse MUST carry the open run's id (else the server overwrites turn_run_id to empty, dropping the dashboard 'working on <run>' link)",
  );
});

test("makeInFlightPulse: tracks the CURRENT inFlight.runId at pulse time (window re-stamped between beats)", async () => {
  const posts = [];
  const inFlight = { submittedAt: Date.now(), completed: false, runId: "run-a" };
  const pulse = makeInFlightPulse({
    httpCall: async () => ({}),
    agentId: "sc-hermes",
    inFlight,
    reportTurnBusyImpl: async (_h, _id, body) => posts.push(body.runId),
  });

  await pulse();
  inFlight.runId = "run-b"; // a newer turn opened the window
  await pulse();

  assert.deepEqual(posts, ["run-a", "run-b"], "each beat reads the live runId");
});

test("makeInFlightPulse: missing/empty runId falls back to '' (no crash, server keeps stale or clears)", async () => {
  const posts = [];
  const pulse = makeInFlightPulse({
    httpCall: async () => ({}),
    agentId: "sc-hermes",
    inFlight: { submittedAt: Date.now(), completed: false, runId: "" },
    reportTurnBusyImpl: async (_h, _id, body) => posts.push(body.runId),
  });
  await pulse();
  assert.equal(posts[0], "", "empty runId threads as '' without throwing");
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

// ---------------------------------------------------------------------------
// runDeliveryLoop lifecycle-ownership (Tasks 1.1, 1.3)
//
// runDeliveryLoop is the SINGLE lifecycle owner of the managed-hermes triad
// (there is intentionally no separate generic seam — the production loop carries
// concurrent probe/re-pulse/connect-refused lifecycles + a return-based terminal
// contract that a generic claimOnce/onReady seam can't host without shim
// adapters + dead hooks). The four lifecycle invariants the old seam asserted
// are covered here against the REAL loop:
//   1. liveness registered BEFORE the gateway bring-up — asserted directly below
//      (injected startLivenessHeartbeat ordered before the gateway-host spawn);
//   2. terminal 410 → teardown + self-exit(0), not swallowed — covered by
//      "runDeliveryLoop: 410 from /dispatch/claim tears down (port-kill) +
//      self-exits(0)";
//   3. release → teardown (not just exit) — covered by "runDeliveryLoop: starts
//      the claim/deliver loop and tears down the gateway host on release";
//   4. transient WS/connect error is NON-terminal (keeps retrying) — covered by
//      "runDeliveryLoop: initial WS connect refused → self-corrects ONCE ...
//      does not spin the signal" (loops maxIterations without terminating).
// ---------------------------------------------------------------------------

test("runDeliveryLoop: registers the liveness heartbeat BEFORE bringing the gateway host up (Task 1.1)", async () => {
  const events = [];
  const { spawn } = makeFakeSpawn();
  // Record the gateway-host spawn relative to the heartbeat registration.
  const spawnImpl = (cmd, args, opts) => {
    events.push("gateway-spawn");
    return spawn(cmd, args, opts);
  };
  const fetchImpl = makeFakeFetch();
  const { httpCall } = makeAifyHttp();
  const ws = makeFakeWsClient({ "session.active_list": ACTIVE_LIST_RESULT });

  await runDeliveryLoop("sc-hermes", {
    httpCall,
    spawnImpl,
    fetchImpl,
    openWs: async () => ws,
    installTeardown: () => {},
    sleepImpl: async () => {},
    serverUrl: "http://127.0.0.1:8800",
    // Injected heartbeat factory: record registration order + return a stop fn.
    startLivenessHeartbeat: ({ beat } = {}) => {
      events.push("liveness-start");
      void beat?.(); // fire once like the real beat (drives the heartbeat POST)
      return () => {};
    },
    maxIterations: 1,
  });

  const livenessIdx = events.indexOf("liveness-start");
  const spawnIdx = events.indexOf("gateway-spawn");
  assert.ok(livenessIdx >= 0, "liveness heartbeat must be registered");
  assert.ok(
    spawnIdx === -1 || livenessIdx < spawnIdx,
    "liveness must register before the gateway host is spawned (no online-with-no-claimer window)",
  );
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

test("runEnsureHostCli: DEFAULT (no ensureSession) skips the dead aify-<id> pre-seed (FIX 2)", async () => {
  // FIX 2 (2026-06-03): pre-seeding the synthetic `aify-<id>` SessionDB row is dead
  // (the native-session-id model resumes the REAL session id), and it littered
  // `hermes sessions list` with an orphan row on EVERY launch. It is now OFF by
  // default — only an explicit `ensureSession: true` opt-in still runs it.
  const { spawn } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch();
  let preseedCalled = false;
  await runEnsureHostCli("sc-hermes", {
    spawnImpl: spawn,
    fetchImpl,
    // No ensureSession key → must NOT pre-seed (the new default).
    spawnSyncImpl: () => { preseedCalled = true; return { status: 0 }; },
    out: () => {},
    err: () => {},
  });
  assert.equal(preseedCalled, false, "default ensure-host must NOT pre-seed the orphan aify-<id> row");
});

test("runEnsureHostCli: ensureSession:true still runs the pre-seed (explicit opt-in seam)", async () => {
  const { spawn } = makeFakeSpawn();
  const fetchImpl = makeFakeFetch();
  let preseedCalled = false;
  await runEnsureHostCli("sc-hermes", {
    spawnImpl: spawn,
    fetchImpl,
    ensureSession: true,
    spawnSyncImpl: () => { preseedCalled = true; return { status: 0 }; },
    out: () => {},
    err: () => {},
  });
  assert.equal(preseedCalled, true, "explicit ensureSession:true opt-in still pre-seeds");
});
