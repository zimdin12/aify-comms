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
  t.after(() => { try { proc.kill("SIGTERM"); } catch {} });
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("fake-hermes did not bind in 5s")), 5000);
    proc.stdout.on("data", (c) => { if (String(c).includes("listening")) { clearTimeout(timeout); resolve(); } });
    proc.on("exit", (code) => reject(new Error(`fake-hermes exited early code=${code}`)));
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

test("resident hermes uses the registered sessionHandle when present", async (t) => {
  const { url, token } = await startFake(t);
  const wsUrl = attachUrl(url, token);

  const { launchRuntimeRun } = await import("../runtimes.js");
  let capturedSessionId = "";
  const controller = launchRuntimeRun({
    agentId: "hermes-resident-handle",
    agentInfo: makeAgentInfo({ gatewayUrl: wsUrl, sessionHandle: "operator-sid-42" }),
    run: makeRun({ id: "run_h_handle" }),
    runtimeState: {},
    callbacks: {
      onEvent: () => {},
      onRefs: (refs) => { if (refs?.sessionId) capturedSessionId = refs.sessionId; },
    },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.ok(!result.failed, `expected hermes dispatch to succeed: ${result.error || ""}`);
  assert.equal(capturedSessionId, "operator-sid-42", "controller should report the registered sessionHandle as the active sessionId");
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
