#!/usr/bin/env node
// HermesSession end-to-end tests using the fake-hermes-acp stdio fixture.
//
// Phases (added in successive commits):
//   B — spawn + handshake (this file's first test)
//   C — runTurn + session/update streaming
//   D — client-method callbacks (fs/read_text_file)
//   E — reuse-across-turns, idle-timeout, handshake-failure heal

import assert from "node:assert/strict";
import path from "node:path";
import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { HermesSession } from "../hermes-session.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FAKE = path.join(__dirname, "fixtures", "fake-hermes-acp.mjs");

// Point the bridge at the fake fixture instead of the real `hermes acp`.
process.env.AIFY_HERMES_ACP_COMMAND = `node ${FAKE}`;

async function test_ensureStarted_completes_handshake() {
  const sess = new HermesSession({
    agentId: "test-agent",
    agentInfo: { runtime: "hermes", mode: "managed", cwd: process.cwd(), runtimeConfig: {} },
  });
  try {
    await sess.ensureStarted();
    assert.equal(typeof sess.sessionId, "string");
    assert.ok(sess.sessionId.startsWith("fake-sess-"), `expected fake-sess-* sessionId, got ${sess.sessionId}`);
    assert.equal(sess._state, "ready");
  } finally {
    await sess.stop();
  }
  console.log("PASS test_ensureStarted_completes_handshake");
}

async function test_runTurn_streams_and_returns_summary() {
  const frames = [];
  const sess = new HermesSession({
    agentId: "test-agent-2",
    agentInfo: { runtime: "hermes", mode: "managed", cwd: process.cwd(), runtimeConfig: {} },
  });
  sess.attachTerminalSink(async (text /*, status */) => { frames.push(text); });
  try {
    await sess.ensureStarted();
    const result = await sess.runTurn({
      promptText: "Say hello",
      run: { id: "run-1", body: "Say hello", subject: "test", from: "operator" },
    });
    assert.equal(result.status, "completed");
    assert.ok(result.summary.includes("hello"), `summary missing 'hello': ${result.summary}`);
    const joined = frames.join("");
    assert.ok(joined.includes("hello") && joined.includes("world"), `terminal frames missing tokens: ${joined.slice(0,200)}`);
  } finally {
    await sess.stop();
  }
  console.log("PASS test_runTurn_streams_and_returns_summary");
}

async function test_client_callback_fs_read() {
  const tmpPath = path.join(process.cwd(), "tmp-hermes-acp-fixture.txt");
  await fs.writeFile(tmpPath, "abc-content-xyz");
  process.env.FAKE_HERMES_ACP_SCRIPT = "client-callback";
  process.env.FAKE_HERMES_ACP_CB_PATH = tmpPath;
  const sess = new HermesSession({
    agentId: "test-agent-cb",
    agentInfo: { runtime: "hermes", mode: "managed", cwd: process.cwd(), runtimeConfig: {} },
  });
  try {
    await sess.ensureStarted();
    const result = await sess.runTurn({ promptText: "go", run: { id: "r" } });
    assert.equal(result.status, "completed");
    assert.ok(result.summary.includes("abc-content-xyz"), `summary missing file content; got ${result.summary}`);
  } finally {
    await sess.stop();
    await fs.unlink(tmpPath).catch(() => {});
    delete process.env.FAKE_HERMES_ACP_SCRIPT;
    delete process.env.FAKE_HERMES_ACP_CB_PATH;
  }
  console.log("PASS test_client_callback_fs_read");
}

async function test_session_reused_across_turns() {
  const sess = new HermesSession({
    agentId: "test-reuse",
    agentInfo: { runtime: "hermes", mode: "managed", cwd: process.cwd(), runtimeConfig: {} },
  });
  try {
    await sess.ensureStarted();
    const firstSessionId = sess.sessionId;
    const r1 = await sess.runTurn({ promptText: "hi", run: { id: "r1" } });
    const r2 = await sess.runTurn({ promptText: "again", run: { id: "r2" } });
    assert.equal(r1.status, "completed");
    assert.equal(r2.status, "completed");
    assert.equal(sess.sessionId, firstSessionId, "sessionId must persist across turns");
  } finally {
    await sess.stop();
  }
  console.log("PASS test_session_reused_across_turns");
}

async function test_idle_timeout_reaps_session() {
  process.env.AIFY_HERMES_IDLE_TIMEOUT_MS = "150";
  const sess = new HermesSession({
    agentId: "test-idle",
    agentInfo: { runtime: "hermes", mode: "managed", cwd: process.cwd(), runtimeConfig: {} },
  });
  try {
    await sess.ensureStarted();
    await sess.runTurn({ promptText: "x", run: { id: "r" } });
    // wait past the idle timeout
    await new Promise((r) => setTimeout(r, 400));
    assert.equal(sess._state, "stopped", `expected stopped after idle, got ${sess._state}`);
  } finally {
    delete process.env.AIFY_HERMES_IDLE_TIMEOUT_MS;
    if (sess._state !== "stopped") await sess.stop();
  }
  console.log("PASS test_idle_timeout_reaps_session");
}

async function test_handshake_failure_marks_failed() {
  process.env.FAKE_HERMES_ACP_SCRIPT = "crash-on-init";
  const sess = new HermesSession({
    agentId: "test-crash",
    agentInfo: { runtime: "hermes", mode: "managed", cwd: process.cwd(), runtimeConfig: { startupTimeoutMs: 2000 } },
  });
  let caught = null;
  try {
    await sess.ensureStarted();
  } catch (e) {
    caught = e;
  } finally {
    delete process.env.FAKE_HERMES_ACP_SCRIPT;
    if (sess._state !== "stopped") await sess.stop();
  }
  assert.ok(caught, "expected ensureStarted to reject when child exits during initialize");
  assert.ok(/timeout|exit/i.test(caught.message), `unexpected error: ${caught.message}`);
  assert.equal(sess._state, "failed");
  console.log("PASS test_handshake_failure_marks_failed");
}

async function test_refusal_marks_failed() {
  process.env.FAKE_HERMES_ACP_SCRIPT = "refuse";
  const sess = new HermesSession({
    agentId: "test-refusal",
    agentInfo: { runtime: "hermes", mode: "managed", cwd: process.cwd(), runtimeConfig: {} },
  });
  try {
    await sess.ensureStarted();
    const result = await sess.runTurn({ promptText: "do bad thing", run: { id: "r" } });
    assert.equal(result.status, "failed");
    assert.match(result.summary, /refus/i);
  } finally {
    delete process.env.FAKE_HERMES_ACP_SCRIPT;
    await sess.stop();
  }
  console.log("PASS test_refusal_marks_failed");
}

async function test_cancel_active_turn() {
  // Use the hello script which has DELAY_MS between chunks; cancel mid-stream.
  process.env.FAKE_HERMES_ACP_DELAY_MS = "80";
  const sess = new HermesSession({
    agentId: "test-cancel",
    agentInfo: { runtime: "hermes", mode: "managed", cwd: process.cwd(), runtimeConfig: {} },
  });
  try {
    await sess.ensureStarted();
    const turnPromise = sess.runTurn({ promptText: "go", run: { id: "r" } });
    // Cancel after ~100ms so the fixture sees the cancel flag during its loop.
    await new Promise((r) => setTimeout(r, 100));
    await sess.cancelActiveTurn();
    const result = await turnPromise;
    assert.equal(result.status, "cancelled");
  } finally {
    delete process.env.FAKE_HERMES_ACP_DELAY_MS;
    await sess.stop();
  }
  console.log("PASS test_cancel_active_turn");
}

await test_ensureStarted_completes_handshake();
await test_runTurn_streams_and_returns_summary();
await test_client_callback_fs_read();
await test_session_reused_across_turns();
await test_idle_timeout_reaps_session();
await test_handshake_failure_marks_failed();
await test_refusal_marks_failed();
await test_cancel_active_turn();
console.log("hermes-session-acp.test.js: all assertions passed");
