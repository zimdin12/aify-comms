#!/usr/bin/env node
// Verifies the post-code-review fix pass for HermesSession + CodexSession.
// Each test maps to a specific finding (C1, C2, I5, I6, I10).

import assert from "node:assert/strict";
import path from "node:path";
import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";
import {
  HermesSession,
  getOrCreateHermesSession,
  _resetHermesSessionPoolForTests,
} from "../hermes-session.js";
import {
  CodexSession,
  getOrCreateCodexSession,
  _resetCodexSessionPoolForTests,
} from "../codex-session.js";
import {
  encodeRequest,
  encodeResponse,
  METHODS,
} from "../hermes-acp-protocol.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FAKE_HERMES = path.join(__dirname, "fixtures", "fake-hermes-acp.mjs");
const FAKE_CODEX = path.join(__dirname, "fixtures", "fake-codex-app-server.mjs");

process.env.AIFY_HERMES_ACP_COMMAND = `node ${FAKE_HERMES}`;
process.env.AIFY_CODEX_COMMAND = `node ${FAKE_CODEX}`;

// ─── C1: getOrCreate evicts terminal-state pool entries ──────────────────────

async function test_C1_pool_heal_hermes() {
  _resetHermesSessionPoolForTests();
  const a = getOrCreateHermesSession({ agentId: "heal-h", agentInfo: { runtime: "hermes", mode: "managed", cwd: process.cwd(), runtimeConfig: {} } });
  // Force the session into 'failed' WITHOUT removing it from the pool —
  // simulates the race where _state→failed but _onExit hasn't fired yet.
  a._state = "failed";
  // Re-lookup must return a fresh, distinct session (not the dead `a`).
  const b = getOrCreateHermesSession({ agentId: "heal-h", agentInfo: { runtime: "hermes", mode: "managed", cwd: process.cwd(), runtimeConfig: {} } });
  assert.notEqual(b, a, "getOrCreate must NOT return the failed instance");
  assert.equal(b._state, "idle");
  await b.stop().catch(() => {});
  _resetHermesSessionPoolForTests();
  console.log("PASS test_C1_pool_heal_hermes");
}

async function test_C1_pool_heal_codex() {
  _resetCodexSessionPoolForTests();
  const a = getOrCreateCodexSession({ agentId: "heal-c", agentInfo: { runtime: "codex", sessionMode: "managed", cwd: process.cwd(), runtimeConfig: {} } });
  a._state = "stopped";
  const b = getOrCreateCodexSession({ agentId: "heal-c", agentInfo: { runtime: "codex", sessionMode: "managed", cwd: process.cwd(), runtimeConfig: {} } });
  assert.notEqual(b, a, "getOrCreate must NOT return the stopped instance");
  assert.equal(b._state, "idle");
  await b.stop().catch(() => {});
  _resetCodexSessionPoolForTests();
  console.log("PASS test_C1_pool_heal_codex");
}

// ─── C2: codex cancelActiveTurn force-settles after grace ────────────────────

async function test_C2_codex_cancel_force_settles_when_app_server_ignores_interrupt() {
  // The "quiet" fake script never emits turn/completed so a real interrupt
  // would also stall. With the C2 fix, cancelActiveTurn force-settles
  // after a grace window so the runTurn promise resolves cancelled.
  process.env.FAKE_CODEX_SCRIPT = "quiet";
  const sess = new CodexSession({ agentId: "cgrace", agentInfo: { runtime: "codex", sessionMode: "managed", cwd: process.cwd(), runtimeConfig: { timeoutMs: 30000, quietTimeoutMs: 0, mcpToolTimeoutMs: 0 } } });
  try {
    await sess.ensureStarted({ runtimeState: {}, callbacks: {} });
    const turnPromise = sess.runTurn({ promptText: "go", run: { id: "r" }, callbacks: {} });
    // Wait for turn/start to have completed (activeTurnId set).
    await new Promise((r) => setTimeout(r, 300));
    await sess.cancelActiveTurn();
    // The grace timer is 5s; await the promise with a 6.5s safety bound.
    const result = await Promise.race([
      turnPromise,
      new Promise((_, rej) => setTimeout(() => rej(new Error("runTurn never resolved after cancel; C2 fix not working")), 6500)),
    ]);
    assert.equal(result.status, "cancelled");
  } finally {
    delete process.env.FAKE_CODEX_SCRIPT;
    await sess.stop();
  }
  console.log("PASS test_C2_codex_cancel_force_settles_when_app_server_ignores_interrupt");
}

// ─── I5: hermes permission auto-approves only safe-allow options ─────────────

async function test_I5_permission_safe_allow_kinds() {
  const sess = new HermesSession({ agentId: "perm-safe", agentInfo: { runtime: "hermes", mode: "managed", cwd: process.cwd(), runtimeConfig: {} } });
  await sess.ensureStarted();
  const responses = [];
  const origWrite = sess._writeRaw.bind(sess);
  sess._writeRaw = (line) => { responses.push(line); origWrite(line); };

  // Case A: safe-allow option present → auto-approve with that option.
  await sess._handleClientRequest({
    id: 9100,
    method: METHODS.SESSION_REQUEST_PERMISSION,
    params: {
      options: [
        { kind: "reject", optionId: "no" },
        { kind: "allow_once", optionId: "yes-once" },
        { kind: "allow_always", optionId: "yes-always" },
      ],
    },
  });
  const a = JSON.parse(responses.shift());
  assert.equal(a.result.outcome.outcome, "selected");
  assert.equal(a.result.outcome.optionId, "yes-once", `expected first allow_once optionId, got ${a.result.outcome.optionId}`);

  // Case B: NO safe-allow option → return outcome.cancelled (not select option[0]).
  await sess._handleClientRequest({
    id: 9101,
    method: METHODS.SESSION_REQUEST_PERMISSION,
    params: {
      options: [
        { kind: "allow_always_no_prompt", optionId: "escalate" },
        { kind: "reject", optionId: "no" },
      ],
    },
  });
  const b = JSON.parse(responses.shift());
  assert.equal(b.result.outcome.outcome, "cancelled", `expected outcome.cancelled when no safe-allow kind offered, got ${b.result.outcome.outcome}`);
  assert.equal(b.result.outcome.optionId, undefined, "cancelled outcome must NOT include an optionId");

  await sess.stop();
  console.log("PASS test_I5_permission_safe_allow_kinds");
}

// ─── I6: hermes fs path-traversal containment ────────────────────────────────

async function test_I6_fs_containment() {
  const sandboxRoot = path.join(process.cwd(), `.tmp-sandbox-${Date.now()}`);
  await fs.mkdir(sandboxRoot, { recursive: true });
  const inside = path.join(sandboxRoot, "ok.txt");
  await fs.writeFile(inside, "inside-ok");

  const sess = new HermesSession({
    agentId: "fs-sandbox",
    agentInfo: { runtime: "hermes", mode: "managed", cwd: sandboxRoot, runtimeConfig: {} },
  });
  await sess.ensureStarted();
  const responses = [];
  const origWrite = sess._writeRaw.bind(sess);
  sess._writeRaw = (line) => { responses.push(line); origWrite(line); };

  // Case A: read inside the sandbox → success.
  await sess._handleClientRequest({
    id: 9200,
    method: METHODS.FS_READ_TEXT_FILE,
    params: { path: inside },
  });
  const a = JSON.parse(responses.shift());
  assert.ok(a.result, `expected result for in-sandbox read, got ${JSON.stringify(a).slice(0, 200)}`);
  assert.equal(a.result.content, "inside-ok");

  // Case B: relative path resolving inside sandbox → success.
  await sess._handleClientRequest({
    id: 9201,
    method: METHODS.FS_READ_TEXT_FILE,
    params: { path: "ok.txt" },
  });
  const b = JSON.parse(responses.shift());
  assert.ok(b.result, "expected result for relative in-sandbox read");
  assert.equal(b.result.content, "inside-ok");

  // Case C: absolute traversal outside sandbox → error -32602.
  const outsidePath = path.resolve(sandboxRoot, "..", "totally-outside.txt");
  await sess._handleClientRequest({
    id: 9202,
    method: METHODS.FS_READ_TEXT_FILE,
    params: { path: outsidePath },
  });
  const c = JSON.parse(responses.shift());
  assert.ok(c.error, `expected error for outside-sandbox read, got ${JSON.stringify(c).slice(0, 200)}`);
  assert.equal(c.error.code, -32602);
  assert.match(c.error.message, /outside the session workspace/);

  // Case D: ../ traversal → blocked.
  await sess._handleClientRequest({
    id: 9203,
    method: METHODS.FS_READ_TEXT_FILE,
    params: { path: "../totally-outside.txt" },
  });
  const d = JSON.parse(responses.shift());
  assert.ok(d.error, "expected error for ../ traversal");
  assert.equal(d.error.code, -32602);

  // Case E: write outside sandbox → blocked.
  await sess._handleClientRequest({
    id: 9204,
    method: METHODS.FS_WRITE_TEXT_FILE,
    params: { path: outsidePath, content: "should never land" },
  });
  const e = JSON.parse(responses.shift());
  assert.ok(e.error, "expected error for outside-sandbox write");
  assert.equal(e.error.code, -32602);
  // Confirm the file does NOT exist (write was actually blocked).
  await assert.rejects(fs.readFile(outsidePath, "utf-8"), /ENOENT/);

  await sess.stop();
  await fs.rm(sandboxRoot, { recursive: true, force: true });
  console.log("PASS test_I6_fs_containment");
}

async function test_I6_fs_unsafe_opt_out() {
  // AIFY_HERMES_FS_UNSAFE=1 must restore unrestricted access.
  const outsideRoot = path.join(process.cwd(), `.tmp-outside-${Date.now()}`);
  await fs.mkdir(outsideRoot, { recursive: true });
  const target = path.join(outsideRoot, "outside.txt");
  await fs.writeFile(target, "unsafe-ok");

  const sandboxRoot = path.join(process.cwd(), `.tmp-sandbox-unsafe-${Date.now()}`);
  await fs.mkdir(sandboxRoot, { recursive: true });

  process.env.AIFY_HERMES_FS_UNSAFE = "1";
  const sess = new HermesSession({ agentId: "fs-unsafe", agentInfo: { runtime: "hermes", mode: "managed", cwd: sandboxRoot, runtimeConfig: {} } });
  try {
    await sess.ensureStarted();
    const responses = [];
    sess._writeRaw = (line) => { responses.push(line); };
    await sess._handleClientRequest({
      id: 9300,
      method: METHODS.FS_READ_TEXT_FILE,
      params: { path: target },
    });
    const r = JSON.parse(responses.shift());
    assert.ok(r.result, `with AIFY_HERMES_FS_UNSAFE=1 reads outside cwd should succeed; got ${JSON.stringify(r).slice(0, 200)}`);
    assert.equal(r.result.content, "unsafe-ok");
  } finally {
    delete process.env.AIFY_HERMES_FS_UNSAFE;
    await sess.stop();
    await fs.rm(outsideRoot, { recursive: true, force: true });
    await fs.rm(sandboxRoot, { recursive: true, force: true });
  }
  console.log("PASS test_I6_fs_unsafe_opt_out");
}

// ─── I10: ensureStarted Deferred barrier — concurrent callers share one start ─

async function test_I10_concurrent_ensureStarted_shares_barrier() {
  _resetHermesSessionPoolForTests();
  const sess = getOrCreateHermesSession({ agentId: "concurrent-h", agentInfo: { runtime: "hermes", mode: "managed", cwd: process.cwd(), runtimeConfig: {} } });
  // Three concurrent ensureStarted calls — all should resolve to the same
  // ready state without each spawning its own child or busy-polling.
  const results = await Promise.all([
    sess.ensureStarted(),
    sess.ensureStarted(),
    sess.ensureStarted(),
  ]);
  // ensureStarted returns undefined on success; the meaningful check is
  // that the session reached 'ready' exactly once.
  assert.equal(sess._state, "ready");
  assert.equal(sess._startupDeferred, null, "_startupDeferred must be cleared after handshake succeeds");
  await sess.stop();
  _resetHermesSessionPoolForTests();
  console.log("PASS test_I10_concurrent_ensureStarted_shares_barrier");
}

await test_C1_pool_heal_hermes();
await test_C1_pool_heal_codex();
await test_C2_codex_cancel_force_settles_when_app_server_ignores_interrupt();
await test_I5_permission_safe_allow_kinds();
await test_I6_fs_containment();
await test_I6_fs_unsafe_opt_out();
await test_I10_concurrent_ensureStarted_shares_barrier();
console.log("session-fixes.test.js: all assertions passed");
