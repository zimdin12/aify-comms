#!/usr/bin/env node
// Unit tests for the per-agent hermes api_server channel sidecar.
//
// The sidecar mirrors claude-channel.js and is WAKE-ONLY: claim a dispatch run
// by agentId (executionModes include "channel"), drive the agent's PINNED hermes
// session via the api_server client (ensureSession + chatStream) to RUN the turn
// to completion, then PATCH the run to `delivered` and clear turn_busy. It does
// NOT post a reply — the in-session hermes agent (which has the aify-comms
// comms_* tools loaded) authors its OWN reply via comms_send + inReplyTo, which
// closes the require_reply run later (exercised in Phase 3/5 tests, not here).
// On chatStream failure the run is PATCHed to a failed state with a cause and the
// loop never crashes.
//
// Tests use dependency injection: a recording fake aify httpCall + the real
// api_server client pointed at the fake-hermes-apiserver fixture.

import assert from "node:assert/strict";
import { test } from "node:test";
import { createHermesApiServerClient } from "../hermes-apiserver-client.js";
import { start } from "./fixtures/fake-hermes-apiserver.mjs";
import { pinnedSessionId } from "../hermes-session-id.js";
import {
  processClaimedRun,
  runPollCycle,
  resolveHermesEndpoint,
  teardownDaemon,
  installShutdownTeardown,
} from "../hermes-channel.js";
import { agentEndpoint } from "../hermes-endpoint.js";

async function withApiServer(t, opts) {
  const fixture = await start(opts);
  t.after(() => fixture.close());
  return fixture;
}

// A recording fake of the aify httpCall(method, endpoint, body) helper.
function makeAifyHttp({ claims = [], release = false } = {}) {
  const calls = [];
  let claimIdx = 0;
  async function httpCall(method, endpoint, body = null) {
    calls.push({ method, endpoint, body });
    if (method === "POST" && endpoint === "/dispatch/claim") {
      // Mode FSM (Task 4.1): the service returns release:true when the agent
      // has been switched to resident so the managed sidecar stops driving.
      if (release) {
        return { ok: true, run: null, release: true };
      }
      const run = claims[claimIdx++];
      return { run: run || null };
    }
    if (method === "POST" && endpoint === "/dispatch/controls/claim") {
      return { controls: [] };
    }
    // heartbeat, turn-end, messages/send, dispatch PATCH → ack
    return { ok: true };
  }
  return { httpCall, calls };
}

function findCall(calls, method, matcher) {
  return calls.find(
    (c) => c.method === method && (typeof matcher === "function" ? matcher(c.endpoint) : c.endpoint === matcher),
  );
}

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

test("processClaimedRun: wake-only — ensures pinned session, chatStreams, marks delivered, posts NO reply", async (t) => {
  const { baseUrl, key } = await withApiServer(t);
  const apiClient = createHermesApiServerClient();
  const { httpCall, calls } = makeAifyHttp();

  await processClaimedRun({
    run: SAMPLE_RUN,
    agentId: "sc-hermes",
    httpCall,
    apiClient,
    baseUrl,
    key,
  });

  // WAKE-ONLY: the sidecar must NOT author a reply. The in-session hermes
  // agent self-replies via comms_send (symmetric with claude-channel.js).
  const sendCalls = calls.filter((c) => c.method === "POST" && c.endpoint === "/messages/send");
  assert.equal(sendCalls.length, 0, "wake-only sidecar must NOT POST /messages/send");

  // Run PATCHed to `delivered` (NOT completed/answered) — the require_reply run
  // stays open until the agent's own comms_send + inReplyTo closes it.
  const patchCall = findCall(calls, "PATCH", (e) => e.startsWith("/dispatch/runs/"));
  assert.ok(patchCall, "expected a PATCH /dispatch/runs/<id>");
  assert.equal(String(patchCall.body.status), "delivered");

  // turn_busy pulsed true (heartbeat) then cleared (turn-end).
  assert.ok(findCall(calls, "POST", (e) => e.endsWith("/heartbeat")), "expected a heartbeat");
  assert.ok(findCall(calls, "POST", (e) => e.endsWith("/turn-end")), "expected a turn-end");
});

test("processClaimedRun: chatStream failure → run PATCHed failed with cause, no throw", async (t) => {
  const { baseUrl } = await withApiServer(t);
  const apiClient = createHermesApiServerClient();
  const { httpCall, calls } = makeAifyHttp();

  // Use the WRONG key so chatStream throws a 401 from the fixture.
  await assert.doesNotReject(() =>
    processClaimedRun({
      run: SAMPLE_RUN,
      agentId: "sc-hermes",
      httpCall,
      apiClient,
      baseUrl,
      key: "wrong-key",
    }),
  );

  // No reply was posted.
  assert.ok(!findCall(calls, "POST", "/messages/send"), "must not post a reply on failure");

  // Run PATCHed to a failed/needs-attention state carrying the cause.
  const patchCall = findCall(calls, "PATCH", (e) => e.startsWith("/dispatch/runs/"));
  assert.ok(patchCall, "expected a PATCH /dispatch/runs/<id>");
  assert.match(String(patchCall.body.status), /failed|needs/i);
  assert.ok(patchCall.body.error || patchCall.body.summary, "expected a failure cause");
  assert.match(String(patchCall.body.error || patchCall.body.summary), /401|api key|unauthor/i);
});

test("runPollCycle: claims a channel run for the agent, drives it, settles it", async (t) => {
  const { baseUrl, key } = await withApiServer(t);
  const apiClient = createHermesApiServerClient();
  const { httpCall, calls } = makeAifyHttp({ claims: [SAMPLE_RUN] });

  await runPollCycle({
    agentId: "sc-hermes",
    machineId: "machine-test",
    bridgeId: "hermes-channel-test",
    httpCall,
    apiClient,
    baseUrl,
    key,
  });

  // Claim was POSTed with the agentId and channel in executionModes.
  const claim = findCall(calls, "POST", "/dispatch/claim");
  assert.ok(claim);
  assert.equal(claim.body.agentId, "sc-hermes");
  assert.ok(Array.isArray(claim.body.executionModes));
  assert.ok(claim.body.executionModes.includes("channel"));
  // Standalone channel sidecar declares bridgeKind so the service accepts the
  // claim on the same basis as claude's (NOT a managed-wrapper-child PTY).
  assert.equal(claim.body.bridgeKind, "channel-sidecar");

  // The claimed run got driven + settled to `delivered` (wake-only: no reply post).
  assert.ok(!findCall(calls, "POST", "/messages/send"), "wake-only: must NOT post a reply");
  const patchCall = findCall(calls, "PATCH", (e) => e.startsWith("/dispatch/runs/"));
  assert.ok(patchCall, "expected a PATCH /dispatch/runs/<id>");
  assert.equal(String(patchCall.body.status), "delivered");
});

test("runPollCycle: pinned session id is derived from agentId (shared with adapter)", async (t) => {
  const { baseUrl, key } = await withApiServer(t);
  // Spy on the api client to capture the sessionId actually used.
  const real = createHermesApiServerClient();
  const seen = {};
  const apiClient = {
    ...real,
    ensureSession: async (args) => { seen.ensure = args.id; return real.ensureSession(args); },
    chatStream: async (args) => { seen.chat = args.sessionId; return real.chatStream(args); },
  };
  const { httpCall } = makeAifyHttp({ claims: [SAMPLE_RUN] });

  await runPollCycle({
    agentId: "sc-hermes",
    machineId: "m",
    bridgeId: "b",
    httpCall,
    apiClient,
    baseUrl,
    key,
  });

  assert.equal(seen.ensure, pinnedSessionId("sc-hermes"));
  assert.equal(seen.chat, pinnedSessionId("sc-hermes"));
});

test("runPollCycle: no claimable run → no chat, no reply, does not throw", async (t) => {
  const { baseUrl, key } = await withApiServer(t);
  const apiClient = createHermesApiServerClient();
  const { httpCall, calls } = makeAifyHttp({ claims: [] });

  await assert.doesNotReject(() =>
    runPollCycle({ agentId: "sc-hermes", machineId: "m", bridgeId: "b", httpCall, apiClient, baseUrl, key }),
  );
  assert.ok(!findCall(calls, "POST", "/messages/send"));
});

test("runPollCycle: release signal → sidecar stops driving (no chat, no claim of work)", async (t) => {
  // Task 4.1 mode FSM: when the agent is switched to resident the service
  // returns release:true on the claim. The managed sidecar must stop driving
  // and surface the release so the poll loop exits (one-driver invariant).
  const { baseUrl, key } = await withApiServer(t);
  const apiClient = createHermesApiServerClient();
  const { httpCall, calls } = makeAifyHttp({ release: true });

  const result = await runPollCycle({
    agentId: "sc-hermes",
    machineId: "m",
    bridgeId: "b",
    httpCall,
    apiClient,
    baseUrl,
    key,
  });

  assert.ok(result && result.released, "runPollCycle must report released=true on a release signal");
  assert.equal(result.processed, 0, "no runs should be processed when released");
  // No work was driven: no chat turn, no reply, no run PATCH.
  assert.ok(!findCall(calls, "POST", "/messages/send"), "released sidecar must not post a reply");
  assert.ok(!findCall(calls, "PATCH", (e) => e.startsWith("/dispatch/runs/")), "released sidecar must not settle a run");
});

test("teardownDaemon: invokes the injected stopDaemon with the agentId (release/shutdown path)", async () => {
  const calls = [];
  const stopDaemon = async (args) => {
    calls.push(args);
    return { stopped: true, pid: 1234 };
  };
  await teardownDaemon({ agentId: "sc-hermes", stopDaemon });
  assert.equal(calls.length, 1, "stopDaemon must be invoked once");
  assert.equal(calls[0].agentId, "sc-hermes");
});

test("teardownDaemon: guards against double teardown (second call is a no-op)", async () => {
  const calls = [];
  const stopDaemon = async (args) => {
    calls.push(args);
    return { stopped: true };
  };
  const state = {};
  await teardownDaemon({ agentId: "sc-hermes", stopDaemon, state });
  await teardownDaemon({ agentId: "sc-hermes", stopDaemon, state });
  assert.equal(calls.length, 1, "double teardown must call stopDaemon only once");
});

test("teardownDaemon: never throws when stopDaemon rejects", async () => {
  const stopDaemon = async () => {
    throw new Error("kill failed");
  };
  await assert.doesNotReject(() => teardownDaemon({ agentId: "sc-hermes", stopDaemon }));
});

test("installShutdownTeardown: wires SIGTERM/SIGINT handlers that call teardown", async () => {
  const registered = {};
  const fakeProc = {
    once(sig, handler) {
      registered[sig] = handler;
    },
    exitCode: undefined,
    exit(code) {
      this.exitCode = code;
    },
  };
  const calls = [];
  const stopDaemon = async (args) => {
    calls.push(args);
    return { stopped: true };
  };
  installShutdownTeardown({
    agentId: "sc-hermes",
    proc: fakeProc,
    stopDaemon,
    state: {},
  });
  assert.equal(typeof registered.SIGTERM, "function", "must register a SIGTERM handler");
  assert.equal(typeof registered.SIGINT, "function", "must register a SIGINT handler");

  // Simulate the signal: the handler must run teardown (stopDaemon) before exit.
  await registered.SIGTERM();
  assert.equal(calls.length, 1, "SIGTERM handler must invoke stopDaemon");
  assert.equal(calls[0].agentId, "sc-hermes");
});

test("resolveHermesEndpoint: env-absent → resolves the per-agent endpoint by agentId", (t) => {
  // This suite runs without AIFY_HERMES_APISERVER_URL/_KEY in env, so the
  // fallback path must derive the SAME deterministic per-agent endpoint the
  // daemon was launched with (so the sidecar talks to its own agent's daemon).
  if (process.env.AIFY_HERMES_APISERVER_URL || process.env.AIFY_HERMES_APISERVER_KEY) {
    t.skip("api_server env override present; env-absent fallback not exercised");
    return;
  }
  const ep = agentEndpoint("sc-hermes");
  const resolved = resolveHermesEndpoint("sc-hermes");
  assert.equal(resolved.baseUrl, ep.baseUrl, "env-absent baseUrl must match agentEndpoint");
  assert.equal(resolved.key, ep.key, "env-absent key must match agentEndpoint");
  // A different agentId resolves a DIFFERENT endpoint (no shared/global daemon).
  // Keys are per-agent random, so they must differ even if ports happen to
  // collide in the hashed port range.
  const other = resolveHermesEndpoint("sc-other");
  assert.notEqual(other.key, resolved.key, "distinct agents → distinct per-agent keys");
});
