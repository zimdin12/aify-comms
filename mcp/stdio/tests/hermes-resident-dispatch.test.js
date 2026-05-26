#!/usr/bin/env node
// End-to-end: resident-hermes dispatch via tui_gateway WS.
//
// Bridge → /dispatch/claim returns a resident hermes run → launchRuntimeRun
// routes to createHermesController → resident + runtimeConfig.gatewayUrl is
// set → createHermesResidentChannelController → opens WS to the local
// gateway → session.most_recent (or registered sessionHandle) → prompt.submit
// → agent.message.delta + agent.message.end → mark dispatch completed.
//
// Pinned by the fake-hermes-gateway.mjs fixture so the wire protocol can't
// silently drift.

import assert from "node:assert/strict";
import { test } from "node:test";
import path from "node:path";
import net from "node:net";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FAKE = path.join(__dirname, "fixtures", "fake-hermes-gateway.mjs");

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
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, FAKE_HERMES_SCRIPT: script, FAKE_HERMES_TOKEN: token },
  });
  t.after(() => {
    try { proc.kill("SIGTERM"); } catch {}
    try { proc.stdout.destroy(); } catch {}
    try { proc.stderr.destroy(); } catch {}
  });
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("fake-hermes did not bind in 5s")), 5000);
    proc.stdout.on("data", (c) => { if (String(c).includes("listening")) { clearTimeout(timeout); resolve(); } });
    proc.on("exit", (code) => reject(new Error(`fake-hermes exited early code=${code}`)));
  });
  try { proc.unref(); } catch {}
  try { proc.stdout.unref(); } catch {}
  try { proc.stderr.unref(); } catch {}
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

test("resident hermes resumes the persisted session_key into a fresh in-memory sid before prompt.submit (Plan 6 follow-up)", async (t) => {
  // Plan 6 follow-up (2026-05-26): the tui_gateway has sid/session_key
  // duality. prompt.submit looks up in-memory `_sessions[sid]` only,
  // never by persisted session_key. External WS clients (us) can't see
  // the operator's TUI sid. session.resume(persisted_key) returns a
  // fresh in-memory sid bound to OUR ws — that sid is then legal for
  // prompt.submit. Observed live 2026-05-26 with sc-hermes-test-1 and
  // hermes-test: prompt.submit on session_key (or any list-derived id)
  // failed with 4001 "session not found".
  const { url, token } = await startFake(t);
  const wsUrl = attachUrl(url, token);

  const { launchRuntimeRun } = await import("../runtimes.js");
  let capturedSessionId = "";
  let capturedSessionKey = "";
  let resumeEvent = "";
  const controller = launchRuntimeRun({
    agentId: "hermes-resident-handle",
    agentInfo: makeAgentInfo({ gatewayUrl: wsUrl, sessionHandle: "operator-sid-42" }),
    run: makeRun({ id: "run_h_handle" }),
    runtimeState: {},
    callbacks: {
      onEvent: (kind, msg) => {
        const s = String(msg || "");
        if (/session\.resume on .* -> sid /.test(s) || /session id corrected/.test(s)) resumeEvent = s;
      },
      onRefs: (refs) => {
        if (refs?.sessionId) capturedSessionId = refs.sessionId;
        if (refs?.sessionKey) capturedSessionKey = refs.sessionKey;
      },
    },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.ok(!result.failed, `expected hermes dispatch to succeed: ${result.error || ""}`);
  assert.match(capturedSessionId, /^mem-/, "controller should adopt the fresh in-memory sid returned by session.resume");
  assert.equal(capturedSessionKey, "operator-sid-42", "registered sessionHandle must win over gateway session.list");
  assert.equal(result.runtimeState?.sessionId, "operator-sid-42", "durable runtimeState.sessionId must remain the persisted Hermes session key");
  assert.match(result.runtimeState?.gatewaySessionId || "", /^mem-/, "short gateway sid should be stored separately from the durable session key");
  assert.match(resumeEvent, /session\.resume on operator-sid-42/, "controller should announce the resume via onEvent");
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
