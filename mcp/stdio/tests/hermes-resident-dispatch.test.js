#!/usr/bin/env node
// End-to-end: resident-hermes dispatch via tui_gateway WS.
//
// Bridge → /dispatch/claim returns a resident hermes run → launchRuntimeRun
// routes to createHermesController → resident + runtimeConfig.gatewayUrl is
// set → createHermesResidentChannelController → opens WS to the local
// gateway → aify.session.bind_transport(active visible TUI) → prompt.submit →
// agent.message.delta + agent.message.end → mark dispatch completed.
//
// Pinned by the fake-hermes-gateway.mjs fixture so the wire protocol can't
// silently drift.

import assert from "node:assert/strict";
import { after, test } from "node:test";
import path from "node:path";
import net from "node:net";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FAKE = path.join(__dirname, "fixtures", "fake-hermes-gateway.mjs");

// Node's test worker can stay alive after the WS child-fixture tests even when
// only stdio handles remain. Exit explicitly after all assertions and cleanup so
// this file is usable in CI without an external timeout.
after(() => {
  setImmediate(() => process.exit(process.exitCode || 0));
});

function pickFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const port = srv.address().port;
      srv.close(() => resolve(port));
    });
  });
}

async function startFake(t, { script = "hello", token = "test-token" } = {}) {
  const port = await pickFreePort();
  const url = `ws://127.0.0.1:${port}`;
  const proc = spawn(process.execPath, [FAKE, "--listen", url], {
    stdio: "ignore",
    env: { ...process.env, FAKE_HERMES_SCRIPT: script, FAKE_HERMES_TOKEN: token },
  });
  const exited = new Promise((resolve) => {
    proc.once("exit", (code, signal) => resolve({ code, signal }));
  });
  t.after(async () => {
    if (proc.exitCode === null && proc.signalCode === null) {
      try { proc.kill("SIGKILL"); } catch {}
      await Promise.race([
        exited,
        new Promise((resolve) => setTimeout(resolve, 500)),
      ]);
    }
  });
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => cleanup(reject, new Error("fake-hermes did not bind in 5s")), 5000);
    let settled = false;
    let retryTimer = null;
    const onExit = (code, signal) => {
      cleanup(reject, new Error(`fake-hermes exited early code=${code} signal=${signal || ""}`));
    };
    const tryConnect = () => {
      if (settled) return;
      const socket = net.createConnection({ host: "127.0.0.1", port });
      socket.once("connect", () => {
        socket.destroy();
        cleanup(resolve);
      });
      socket.once("error", () => {
        socket.destroy();
        retryTimer = setTimeout(tryConnect, 25);
      });
    };
    const cleanup = (finish, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      if (retryTimer) clearTimeout(retryTimer);
      proc.off("exit", onExit);
      finish(value);
    };
    proc.once("exit", onExit);
    tryConnect();
  });
  return { url, token };
}

function attachUrl(base, token) {
  return `${base}/api/ws?token=${token}`;
}

function makeAgentInfo({ gatewayUrl, sessionHandle = "" }) {
  return {
    agentId: "hermes-resident-test",
    runtime: "hermes",
    sessionMode: "resident",
    sessionHandle,
    cwd: process.cwd(),
    capabilities: ["resident-run"],
    runtimeConfig: { gatewayUrl },
  };
}

function makeRun(extra = {}) {
  return {
    id: "run_h_test",
    executionMode: "resident",
    subject: "Wake test",
    body: "Hello hermes from the bridge",
    from: "agent-a",
    ...extra,
  };
}

test("resident hermes dispatch sends prompt.submit and resolves on agent.message.end", async (t) => {
  const { url, token } = await startFake(t);
  const wsUrl = attachUrl(url, token);

  const { launchRuntimeRun } = await import("../runtimes.js");
  const events = [];
  const frames = [];
  const sinkProvider = async () => async (text, status) => { frames.push({ text: String(text || ""), status: String(status || "") }); };

  const controller = launchRuntimeRun({
    agentId: "hermes-resident-test",
    agentInfo: makeAgentInfo({ gatewayUrl: wsUrl }),
    run: makeRun(),
    runtimeState: {},
    callbacks: {
      onEvent: (kind, msg) => events.push({ kind, msg }),
      onRefs: () => {},
      terminalSinkProvider: sinkProvider,
    },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.ok(!result.failed, `expected resident hermes dispatch to succeed: ${result.error || ""}`);
  assert.equal(result.status, "completed");
  assert.match(result.summary || "", /hello from hermes/);

  const allText = frames.map((f) => f.text).join("");
  assert.match(allText, /Hello hermes from the bridge/, "synth-terminal should echo the dispatch body");
  assert.match(allText, /hello from hermes/, "synth-terminal should reflect streamed reply");
});

test("resident hermes without gateway refuses hidden single-shot fallback", async () => {
  const { launchRuntimeRun } = await import("../runtimes.js");
  const controller = launchRuntimeRun({
    agentId: "hermes-resident-no-gateway",
    agentInfo: {
      ...makeAgentInfo({ gatewayUrl: "", sessionHandle: "operator-sid-42" }),
      runtimeConfig: {},
    },
    run: makeRun({ id: "run_h_no_gateway" }),
    runtimeState: {},
    callbacks: { onEvent: () => {}, onRefs: () => {} },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.equal(result.failed, true, "resident Hermes without gateway must not fork a hidden session");
  assert.match(result.error || "", /gatewayUrl|hidden single-shot/i);
});

test("channel hermes without gateway refuses hidden single-shot fallback", async () => {
  const { launchRuntimeRun } = await import("../runtimes.js");
  const controller = launchRuntimeRun({
    agentId: "hermes-channel-no-gateway",
    agentInfo: {
      ...makeAgentInfo({ gatewayUrl: "", sessionHandle: "operator-sid-42" }),
      sessionMode: "managed",
      runtimeConfig: {},
    },
    run: makeRun({ id: "run_h_channel_no_gateway", executionMode: "channel" }),
    runtimeState: {},
    callbacks: { onEvent: () => {}, onRefs: () => {} },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.equal(result.failed, true, "managed/channel Hermes without gateway must not fork a hidden session");
  assert.match(result.error || "", /gatewayUrl|hidden single-shot/i);
});

test("resident hermes dispatch resolves on real tui_gateway event envelopes", async (t) => {
  const { url, token } = await startFake(t, { script: "enveloped" });
  const wsUrl = attachUrl(url, token);

  const { launchRuntimeRun } = await import("../runtimes.js");
  const frames = [];
  const controller = launchRuntimeRun({
    agentId: "hermes-resident-enveloped",
    agentInfo: makeAgentInfo({ gatewayUrl: wsUrl }),
    run: makeRun({ id: "run_h_enveloped" }),
    runtimeState: {},
    callbacks: {
      onEvent: () => {},
      onRefs: () => {},
      terminalSinkProvider: async () => async (text, status) => { frames.push({ text: String(text || ""), status: String(status || "") }); },
    },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.ok(!result.failed, `expected enveloped resident hermes dispatch to succeed: ${result.error || ""}`);
  assert.equal(result.status, "completed");
  assert.match(result.summary || "", /hello from hermes/);
  assert.match(frames.map((f) => f.text).join(""), /hello from hermes/);
});

test("resident hermes renders a visible aify notice before prompt.submit", async (t) => {
  const { url, token } = await startFake(t, { script: "require_notice" });
  const wsUrl = attachUrl(url, token);

  const { launchRuntimeRun } = await import("../runtimes.js");
  const { buildHermesVisibleWakeNotice } = await import("../controllers/hermes-resident-controller.js");
  const controller = launchRuntimeRun({
    agentId: "hermes-resident-visible-notice",
    agentInfo: makeAgentInfo({ gatewayUrl: wsUrl, sessionHandle: "operator-sid-42" }),
    run: makeRun({ id: "run_h_visible_notice" }),
    runtimeState: {},
    callbacks: { onEvent: () => {}, onRefs: () => {} },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.ok(!result.failed, `expected visible notice before prompt.submit: ${result.error || ""}`);
  assert.equal(result.status, "completed");

  const notice = buildHermesVisibleWakeNotice({
    from: "agent-a",
    to: "hermes-resident-visible-notice",
    subject: "Wake test",
    body: "Hello hermes from the bridge",
  });
  assert.match(notice, /^\+-+\+$/m);
  assert.match(notice, /\| aify-comms message\s+\|/);
  assert.match(notice, /\| agent-a -> hermes-resident-visible-notice\s+\|/);
  assert.match(notice, /\| Subject: Wake test\s+\|/);
  assert.match(notice, /Hello hermes from the bridge/);
  assert.doesNotMatch(notice, /AIFY-COMMS DELIVERY INSTRUCTIONS/);
  assert.doesNotMatch(notice, /\n\s{2,}Subject:/);
});

test("resident hermes wake prompt tells Hermes final text is the comms reply", async (t) => {
  const { url, token } = await startFake(t, { script: "echo_prompt" });
  const wsUrl = attachUrl(url, token);

  const { launchRuntimeRun } = await import("../runtimes.js");
  const events = [];
  const controller = launchRuntimeRun({
    agentId: "hermes-resident-prompt-contract",
    agentInfo: makeAgentInfo({ gatewayUrl: wsUrl }),
    run: makeRun({ id: "run_h_prompt_contract", body: "Please ping back." }),
    runtimeState: {},
    callbacks: {
      onEvent: (kind, msg) => events.push({ kind, msg }),
      onRefs: () => {},
    },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.ok(!result.failed, `expected prompt-contract dispatch to succeed: ${result.error || ""}`);
  assert.match(result.summary || "", /Your final assistant response is captured and posted back/);
  assert.match(result.summary || "", /Do not call comms_send, local HTTP, curl, browser, or terminal tools/);
  assert.match(result.summary || "", /\| aify-comms message\s+\|/);
  assert.match(result.summary || "", /\| agent-a -> hermes-resident-prompt-contract\s+\|/);
  assert.doesNotMatch(result.summary || "", /\[aify-comms wake from/);
  assert.doesNotMatch(result.summary || "", /\nSubject:/);
  assert.ok(events.some((e) => /turn completed/i.test(String(e.msg || ""))), "expected a visible completion event");
});

test("resident hermes dispatch fails promptly on message.complete status=error", async (t) => {
  const { url, token } = await startFake(t, { script: "turn_error" });
  const wsUrl = attachUrl(url, token);

  const { launchRuntimeRun } = await import("../runtimes.js");
  const controller = launchRuntimeRun({
    agentId: "hermes-resident-turn-error",
    agentInfo: makeAgentInfo({ gatewayUrl: wsUrl }),
    run: makeRun({ id: "run_h_turn_error" }),
    runtimeState: {},
    callbacks: { onEvent: () => {}, onRefs: () => {} },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.equal(result.failed, true, "expected Hermes turn error to reject the dispatch");
  assert.match(result.error || "", /provider unavailable/);
});

test("resident hermes falls back to session.steer when prompt.submit returns 4009 busy", async (t) => {
  const { url, token } = await startFake(t, { script: "busy" });
  const wsUrl = attachUrl(url, token);

  const { launchRuntimeRun } = await import("../runtimes.js");
  const events = [];

  const controller = launchRuntimeRun({
    agentId: "hermes-resident-busy",
    agentInfo: makeAgentInfo({ gatewayUrl: wsUrl }),
    run: makeRun({ id: "run_h_busy", body: "mid-run insertion" }),
    runtimeState: {},
    callbacks: {
      onEvent: (kind, msg) => events.push({ kind, msg }),
      onRefs: () => {},
    },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.ok(!result.failed, `expected steer-fallback to succeed: ${result.error || ""}`);
  assert.equal(result.status, "completed");
  assert.match(result.summary || "", /Steered into running turn/);

  const steerEvents = events.filter((e) => /steer/i.test(String(e.msg || "")));
  assert.ok(steerEvents.length >= 1, "expected at least one steer-related onEvent emission");
});

test("resident hermes binds to the visible TUI session instead of resuming a hidden sid", async (t) => {
  // Harness console contract: resident delivery must wake the session the
  // operator is watching. The bridge asks the patched Hermes gateway to bind
  // its WS as a mirror on the active TUI sid, then submits to that sid. It
  // must not call session.resume/session.create because those fork hidden
  // in-memory sessions that never render in the open terminal.
  const { url, token } = await startFake(t);
  const wsUrl = attachUrl(url, token);

  const { launchRuntimeRun } = await import("../runtimes.js");
  let capturedSessionId = "";
  let capturedSessionKey = "";
  let bindEvent = "";
  const controller = launchRuntimeRun({
    agentId: "hermes-resident-handle",
    agentInfo: makeAgentInfo({ gatewayUrl: wsUrl, sessionHandle: "operator-sid-42" }),
    run: makeRun({ id: "run_h_handle" }),
    runtimeState: {},
    callbacks: {
      onEvent: (kind, msg) => {
        const s = String(msg || "");
        if (/visible session bound/.test(s)) bindEvent = s;
      },
      onRefs: (refs) => {
        if (refs?.sessionId) capturedSessionId = refs.sessionId;
        if (refs?.sessionKey) capturedSessionKey = refs.sessionKey;
      },
    },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.ok(!result.failed, `expected hermes dispatch to succeed: ${result.error || ""}`);
  assert.equal(capturedSessionId, "live-sid-001", "controller should submit to the active visible TUI sid");
  assert.equal(capturedSessionKey, "operator-sid-42", "registered sessionHandle must win over gateway session.list");
  assert.equal(result.runtimeState?.sessionId, "operator-sid-42", "durable runtimeState.sessionId must remain the persisted Hermes session key");
  assert.equal(result.runtimeState?.gatewaySessionId, "live-sid-001", "short gateway sid should be stored separately from the durable session key");
  assert.match(bindEvent, /visible session bound: operator-sid-42 -> live-sid-001/, "controller should announce visible-session binding");
});

test("resident hermes updates durable key from visible bind when saved handle is stale", async (t) => {
  const { url, token } = await startFake(t, { script: "bind_actual_key" });
  const wsUrl = attachUrl(url, token);

  const { launchRuntimeRun } = await import("../runtimes.js");
  let capturedSessionId = "";
  let capturedSessionKey = "";
  const controller = launchRuntimeRun({
    agentId: "hermes-resident-stale-handle",
    agentInfo: makeAgentInfo({ gatewayUrl: wsUrl, sessionHandle: "stale-saved-key" }),
    run: makeRun({ id: "run_h_stale_handle" }),
    runtimeState: {},
    callbacks: {
      onEvent: () => {},
      onRefs: (refs) => {
        if (refs?.sessionId) capturedSessionId = refs.sessionId;
        if (refs?.sessionKey) capturedSessionKey = refs.sessionKey;
      },
    },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.ok(!result.failed, `expected stale saved handle to bind to the actual visible session: ${result.error || ""}`);
  assert.equal(capturedSessionId, "live-sid-001");
  assert.equal(capturedSessionKey, "actual-visible-key");
  assert.equal(result.runtimeState?.sessionId, "actual-visible-key");
  assert.equal(result.runtimeState?.gatewaySessionId, "live-sid-001");
});

test("resident hermes retries visible bind with current active session when saved key is gone", async (t) => {
  const { url, token } = await startFake(t, { script: "bind_stale_then_active" });
  const wsUrl = attachUrl(url, token);

  const { launchRuntimeRun } = await import("../runtimes.js");
  const events = [];
  let capturedSessionId = "";
  let capturedSessionKey = "";
  const controller = launchRuntimeRun({
    agentId: "hermes-resident-stale-bind",
    agentInfo: makeAgentInfo({ gatewayUrl: wsUrl, sessionHandle: "stale-saved-key" }),
    run: makeRun({ id: "run_h_stale_bind" }),
    runtimeState: {},
    callbacks: {
      onEvent: (kind, msg) => events.push(String(msg || "")),
      onRefs: (refs) => {
        if (refs?.sessionId) capturedSessionId = refs.sessionId;
        if (refs?.sessionKey) capturedSessionKey = refs.sessionKey;
      },
    },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.ok(!result.failed, `expected stale saved key to retry against current visible session: ${result.error || ""}`);
  assert.equal(capturedSessionId, "live-sid-001");
  assert.equal(capturedSessionKey, "active-visible-key");
  assert.equal(result.runtimeState?.sessionId, "active-visible-key");
  assert.equal(result.runtimeState?.gatewaySessionId, "live-sid-001");
  assert.ok(events.some((event) => /visible session bind retry: stale-saved-key -> active-visible-key/.test(event)));
});

test("resident hermes fails visibly when the gateway lacks visible-session binding", async (t) => {
  const { url, token } = await startFake(t, { script: "no_visible_bind" });
  const wsUrl = attachUrl(url, token);

  const { launchRuntimeRun } = await import("../runtimes.js");
  const controller = launchRuntimeRun({
    agentId: "hermes-resident-no-bind",
    agentInfo: makeAgentInfo({ gatewayUrl: wsUrl, sessionHandle: "operator-sid-42" }),
    run: makeRun({ id: "run_h_no_bind" }),
    runtimeState: {},
    callbacks: { onEvent: () => {}, onRefs: () => {} },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.equal(result.failed, true, "expected old Hermes gateway to fail instead of forking a hidden session");
  assert.match(result.error || "", /visible-session binding/i);
});

test("managed hermes with empty sessionHandle refuses gateway-global fallback", async (t) => {
  // #135: two managed hermes agents sharing the gateway's global state.db both
  // resolved the globally-most-recent session id and bound the SAME visible
  // session. A managed agent must bind ONLY its own session; if it has none it
  // must fail visibly rather than fall back to session.list/session.most_recent.
  const { url, token } = await startFake(t);
  const wsUrl = attachUrl(url, token);

  const { launchRuntimeRun } = await import("../runtimes.js");
  const events = [];
  const controller = launchRuntimeRun({
    agentId: "hermes-managed-empty",
    agentInfo: {
      ...makeAgentInfo({ gatewayUrl: wsUrl, sessionHandle: "" }),
      sessionMode: "managed",
    },
    run: makeRun({ id: "run_h_managed_empty", executionMode: "channel" }),
    runtimeState: {},
    callbacks: {
      onEvent: (kind, msg) => events.push(String(msg || "")),
      onRefs: () => {},
    },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.equal(result.failed, true, "managed hermes without own session must fail, not bind gateway-global");
  assert.match(result.error || "", /no own visible session/i, "must use the managed refusal message");
  assert.match(result.error || "", /Restart hermes-aify/i, "must tell the operator to restart hermes-aify");
  // Prove it never bound or submitted against the gateway-global most_recent/list session.
  assert.ok(!events.some((e) => /visible session bound/i.test(e)), "must NOT bind the gateway-global session");
  assert.ok(!events.some((e) => /prompt\.submit on session/i.test(e)), "must NOT prompt.submit on a global session");
});

test("managed hermes with valid sessionHandle binds its own session and proceeds", async (t) => {
  const { url, token } = await startFake(t);
  const wsUrl = attachUrl(url, token);

  const { launchRuntimeRun } = await import("../runtimes.js");
  const events = [];
  let capturedSessionKey = "";
  let capturedSessionId = "";
  const controller = launchRuntimeRun({
    agentId: "hermes-managed-own",
    agentInfo: {
      ...makeAgentInfo({ gatewayUrl: wsUrl, sessionHandle: "own-managed-sid" }),
      sessionMode: "managed",
    },
    run: makeRun({ id: "run_h_managed_own", executionMode: "channel" }),
    runtimeState: {},
    callbacks: {
      onEvent: (kind, msg) => events.push(String(msg || "")),
      onRefs: (refs) => {
        if (refs?.sessionKey) capturedSessionKey = refs.sessionKey;
        if (refs?.sessionId) capturedSessionId = refs.sessionId;
      },
    },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.ok(!result.failed, `expected managed hermes with own session to succeed: ${result.error || ""}`);
  assert.equal(result.status, "completed");
  assert.match(result.summary || "", /hello from hermes/);
  assert.equal(capturedSessionKey, "own-managed-sid", "managed agent must bind its OWN registered session key");
  assert.equal(capturedSessionId, "live-sid-001");
  assert.ok(events.some((e) => /prompt\.submit on session live-sid-001/i.test(e)), "must submit to its own visible sid");
});

test("resident hermes with empty sessionHandle still falls back to gateway most_recent", async (t) => {
  // Resident = the single visible TUI on the box. Gateway-global fallback is
  // correct here and must remain unchanged.
  const { url, token } = await startFake(t);
  const wsUrl = attachUrl(url, token);

  const { launchRuntimeRun } = await import("../runtimes.js");
  const events = [];
  let capturedSessionKey = "";
  const controller = launchRuntimeRun({
    agentId: "hermes-resident-empty-fallback",
    agentInfo: makeAgentInfo({ gatewayUrl: wsUrl, sessionHandle: "" }),
    run: makeRun({ id: "run_h_resident_empty" }),
    runtimeState: {},
    callbacks: {
      onEvent: (kind, msg) => events.push(String(msg || "")),
      onRefs: (refs) => { if (refs?.sessionKey) capturedSessionKey = refs.sessionKey; },
    },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.ok(!result.failed, `expected resident fallback to succeed: ${result.error || ""}`);
  assert.equal(result.status, "completed");
  assert.match(result.summary || "", /hello from hermes/);
  // session.list returns sess-fake-001 first, so the resident path resolves+binds it.
  assert.equal(capturedSessionKey, "sess-fake-001", "resident must resolve the gateway-global session");
});

test("resident hermes rejects connection when token is wrong", async (t) => {
  const { url } = await startFake(t, { token: "correct-token" });
  const wsUrl = attachUrl(url, "WRONG-TOKEN");

  const { launchRuntimeRun } = await import("../runtimes.js");
  const controller = launchRuntimeRun({
    agentId: "hermes-resident-badauth",
    agentInfo: makeAgentInfo({ gatewayUrl: wsUrl }),
    run: makeRun({ id: "run_h_badauth" }),
    runtimeState: {},
    callbacks: { onEvent: () => {}, onRefs: () => {} },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.ok(result.failed, "expected bad-token dispatch to fail, not silently complete");
  assert.match(result.error || "", /closed|gateway|token|4001/i);
});
