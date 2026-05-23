#!/usr/bin/env node
// CodexSession end-to-end tests using the fake-codex-app-server fixture.
// Mirror of hermes-session-acp.test.js — exercises spawn+handshake,
// runTurn with streaming notifications, session reuse across turns,
// idle-timeout reaper, mid-turn interrupt, refusal.

import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { CodexSession } from "../codex-session.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FAKE = path.join(__dirname, "fixtures", "fake-codex-app-server.mjs");

// Tell the bridge to launch the fake codex app-server in place of the real one.
process.env.AIFY_CODEX_COMMAND = `node ${FAKE}`;

function makeAgentInfo(extra = {}) {
  return {
    runtime: "codex",
    sessionMode: "managed",
    cwd: process.cwd(),
    runtimeConfig: { codexAppServerUrl: "", timeoutMs: 30000, quietTimeoutMs: 0, mcpToolTimeoutMs: 0, ...(extra.runtimeConfig || {}) },
    ...extra,
  };
}

async function test_ensureStarted_starts_thread() {
  const sess = new CodexSession({ agentId: "cx1", agentInfo: makeAgentInfo() });
  try {
    await sess.ensureStarted({ runtimeState: {}, callbacks: {} });
    assert.ok(sess.threadId.startsWith("fake-thread-"), `expected fake-thread-* threadId, got ${sess.threadId}`);
    assert.equal(sess._state, "ready");
  } finally { await sess.stop(); }
  console.log("PASS test_ensureStarted_starts_thread");
}

async function test_runTurn_streams_deltas_and_returns_summary() {
  const frames = [];
  const sess = new CodexSession({ agentId: "cx2", agentInfo: makeAgentInfo() });
  sess.attachTerminalSink(async (text) => { frames.push(text); });
  try {
    await sess.ensureStarted({ runtimeState: {}, callbacks: {} });
    const result = await sess.runTurn({
      promptText: "say hello",
      run: { id: "r1", body: "say hello", subject: "test", from: "operator" },
      callbacks: { onEvent: () => {}, onRefs: () => {} },
    });
    assert.equal(result.status, "completed");
    assert.ok(result.summary.includes("hello"), `summary missing 'hello': ${result.summary}`);
    const joined = frames.join("");
    assert.ok(joined.includes("hello") && joined.includes("world"), `frames missing tokens: ${joined.slice(0, 200)}`);
  } finally { await sess.stop(); }
  console.log("PASS test_runTurn_streams_deltas_and_returns_summary");
}

async function test_session_reused_across_turns() {
  const sess = new CodexSession({ agentId: "cx3", agentInfo: makeAgentInfo() });
  try {
    await sess.ensureStarted({ runtimeState: {}, callbacks: {} });
    const firstThreadId = sess.threadId;
    const r1 = await sess.runTurn({ promptText: "hi", run: { id: "r1" }, callbacks: {} });
    const r2 = await sess.runTurn({ promptText: "again", run: { id: "r2" }, callbacks: {} });
    assert.equal(r1.status, "completed");
    assert.equal(r2.status, "completed");
    assert.equal(sess.threadId, firstThreadId, "threadId must persist across turns");
  } finally { await sess.stop(); }
  console.log("PASS test_session_reused_across_turns");
}

async function test_resume_existing_thread() {
  const sess = new CodexSession({ agentId: "cx4", agentInfo: makeAgentInfo() });
  try {
    await sess.ensureStarted({ runtimeState: { threadId: "existing-thread-abc" }, callbacks: {} });
    assert.equal(sess.threadId, "existing-thread-abc", `expected resumed threadId, got ${sess.threadId}`);
  } finally { await sess.stop(); }
  console.log("PASS test_resume_existing_thread");
}

async function test_idle_timeout_reaps_session() {
  process.env.AIFY_CODEX_IDLE_TIMEOUT_MS = "150";
  const sess = new CodexSession({ agentId: "cx5", agentInfo: makeAgentInfo() });
  try {
    await sess.ensureStarted({ runtimeState: {}, callbacks: {} });
    await sess.runTurn({ promptText: "x", run: { id: "r" }, callbacks: {} });
    await new Promise((r) => setTimeout(r, 400));
    assert.equal(sess._state, "stopped", `expected stopped after idle, got ${sess._state}`);
  } finally {
    delete process.env.AIFY_CODEX_IDLE_TIMEOUT_MS;
    if (sess._state !== "stopped") await sess.stop();
  }
  console.log("PASS test_idle_timeout_reaps_session");
}

async function test_handshake_failure_marks_failed() {
  process.env.FAKE_CODEX_SCRIPT = "crash-on-init";
  const sess = new CodexSession({ agentId: "cx6", agentInfo: makeAgentInfo({ runtimeConfig: { startupTimeoutMs: 2000 } }) });
  let caught = null;
  try {
    await sess.ensureStarted({ runtimeState: {}, callbacks: {} });
  } catch (e) { caught = e; }
  finally {
    delete process.env.FAKE_CODEX_SCRIPT;
    if (sess._state !== "stopped") await sess.stop();
  }
  assert.ok(caught, "expected ensureStarted to reject when child exits during initialize");
  assert.equal(sess._state, "failed");
  console.log("PASS test_handshake_failure_marks_failed");
}

async function test_refusal_rejects_turn() {
  process.env.FAKE_CODEX_SCRIPT = "refuse";
  const sess = new CodexSession({ agentId: "cx7", agentInfo: makeAgentInfo() });
  let caught = null;
  try {
    await sess.ensureStarted({ runtimeState: {}, callbacks: {} });
    try {
      await sess.runTurn({ promptText: "do bad thing", run: { id: "r" }, callbacks: {} });
    } catch (e) { caught = e; }
  } finally {
    delete process.env.FAKE_CODEX_SCRIPT;
    await sess.stop();
  }
  assert.ok(caught, "expected refusal to reject");
  assert.match(caught.message, /policy denied|status failed/i);
  console.log("PASS test_refusal_rejects_turn");
}

async function test_interrupt_active_turn() {
  process.env.FAKE_CODEX_SCRIPT = "interrupt";
  process.env.FAKE_CODEX_DELAY_MS = "80";
  const sess = new CodexSession({ agentId: "cx8", agentInfo: makeAgentInfo() });
  try {
    await sess.ensureStarted({ runtimeState: {}, callbacks: {} });
    const turnPromise = sess.runTurn({ promptText: "go", run: { id: "r" }, callbacks: {} });
    await new Promise((r) => setTimeout(r, 200));
    await sess.cancelActiveTurn();
    const result = await turnPromise;
    assert.equal(result.status, "cancelled");
  } finally {
    delete process.env.FAKE_CODEX_SCRIPT;
    delete process.env.FAKE_CODEX_DELAY_MS;
    await sess.stop();
  }
  console.log("PASS test_interrupt_active_turn");
}

await test_ensureStarted_starts_thread();
await test_runTurn_streams_deltas_and_returns_summary();
await test_session_reused_across_turns();
await test_resume_existing_thread();
await test_idle_timeout_reaps_session();
await test_handshake_failure_marks_failed();
await test_refusal_rejects_turn();
await test_interrupt_active_turn();
console.log("codex-session.test.js: all assertions passed");
