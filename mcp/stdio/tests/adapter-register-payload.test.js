import assert from "assert";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { adapterFor } from "../adapters/index.js";

// Helper extracted from server.js's comms_register handler. Once Plan 1 lands,
// this helper lives at mcp/stdio/register-helpers.js and is exported for
// testing via fillSessionHandleFromAdapter.
import { fillSessionHandleFromAdapter } from "../register-helpers.js";
import { tmpDir } from "./_tmpdir.js";

// Every claude case below passes an explicit `dir` (the agent-keyed capture store) and
// `homeDir` (the project-transcript scan root). Without them the discovery fallback added
// 2026-08-19 reads the OPERATOR'S live store and transcripts, so a test would pass or fail
// on what happens to be on this machine. Sealing is not optional here: the fallback exists
// precisely to find real files.
function sealedClaude(overrides = {}) {
  const dir = tmpDir("aify-register-seal-");
  return { dir, homeDir: dir, cwd: path.join(dir, "nowhere"), ...overrides };
}

test("fillSessionHandleFromAdapter preserves caller-supplied handle", async () => {
  process.env.CLAUDE_SESSION_ID = "from-env";
  const adapter = adapterFor("claude-code");
  const args = { agentId: "a", sessionHandle: "caller-handle" };
  const out = await fillSessionHandleFromAdapter(args, adapter, sealedClaude());
  assert.strictEqual(out.sessionHandle, "caller-handle");
  delete process.env.CLAUDE_SESSION_ID;
});

test("fillSessionHandleFromAdapter fills empty sessionHandle from adapter env", async () => {
  process.env.CLAUDE_SESSION_ID = "from-env";
  const adapter = adapterFor("claude-code");
  const args = { agentId: "a" };
  const out = await fillSessionHandleFromAdapter(args, adapter, sealedClaude());
  assert.strictEqual(out.sessionHandle, "from-env");
  delete process.env.CLAUDE_SESSION_ID;
});

test("fillSessionHandleFromAdapter fills Hermes handle from the REAL env session id", async () => {
  // Native-session-id model (2026-06-03, Task 4 — reverts e89af02): hermes is
  // treated like every other runtime. The real visible session id (from the
  // adapter's env) becomes the handle; no synthetic `aify-<id>` override, and
  // the handle is NOT suppressed for a live gateway (deliverability keys on the
  // gateway, not the handle).
  const activeFile = process.env.AIFY_HERMES_ACTIVE_SESSION_FILE;
  const tuiActiveFile = process.env.HERMES_TUI_ACTIVE_SESSION_FILE;
  delete process.env.AIFY_HERMES_ACTIVE_SESSION_FILE;
  delete process.env.HERMES_TUI_ACTIVE_SESSION_FILE;
  process.env.HERMES_SESSION_ID = "20260603_real_visible_session";
  process.env.AIFY_HERMES_GATEWAY_URL = "ws://127.0.0.1:9999/api/ws?token=x";
  try {
    const adapter = adapterFor("hermes");
    const args = { agentId: "h" };
    const out = await fillSessionHandleFromAdapter(args, adapter);
    assert.strictEqual(out.sessionHandle, "20260603_real_visible_session");
    assert.ok(!/^aify-/.test(out.sessionHandle), "must not be a synthetic aify-<id> name");
  } finally {
    delete process.env.HERMES_SESSION_ID;
    delete process.env.AIFY_HERMES_GATEWAY_URL;
    if (activeFile === undefined) delete process.env.AIFY_HERMES_ACTIVE_SESSION_FILE;
    else process.env.AIFY_HERMES_ACTIVE_SESSION_FILE = activeFile;
    if (tuiActiveFile === undefined) delete process.env.HERMES_TUI_ACTIVE_SESSION_FILE;
    else process.env.HERMES_TUI_ACTIVE_SESSION_FILE = tuiActiveFile;
  }
});

test("fillSessionHandleFromAdapter fills Hermes handle from the live TUI active-session file", async () => {
  const dir = tmpDir("aify-register-hermes-");
  const activeFile = path.join(dir, "active.json");
  fs.writeFileSync(activeFile, JSON.stringify({ session_id: "20260715_live_plain_tui" }));
  process.env.AIFY_HERMES_ACTIVE_SESSION_FILE = activeFile;
  delete process.env.HERMES_SESSION_ID;
  try {
    const adapter = adapterFor("hermes");
    const out = await fillSessionHandleFromAdapter({ agentId: "plain-hermes" }, adapter);
    assert.strictEqual(out.sessionHandle, "20260715_live_plain_tui");
  } finally {
    delete process.env.AIFY_HERMES_ACTIVE_SESSION_FILE;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("fillSessionHandleFromAdapter preserves a caller-supplied Hermes handle", async () => {
  process.env.HERMES_SESSION_ID = "env-session-should-not-override";
  process.env.AIFY_HERMES_GATEWAY_URL = "ws://127.0.0.1:9999/api/ws?token=x";
  const adapter = adapterFor("hermes");
  const args = { agentId: "h", sessionHandle: "caller-supplied-real-id" };
  const out = await fillSessionHandleFromAdapter(args, adapter);
  assert.strictEqual(out.sessionHandle, "caller-supplied-real-id");
  delete process.env.HERMES_SESSION_ID;
  delete process.env.AIFY_HERMES_GATEWAY_URL;
});

test("fillSessionHandleFromAdapter leaves empty when env has no handle and nothing is discoverable", async () => {
  delete process.env.CLAUDE_SESSION_ID;
  const adapter = adapterFor("claude-code");
  const args = { agentId: "a" };
  const out = await fillSessionHandleFromAdapter(args, adapter, sealedClaude());
  assert.strictEqual(out.sessionHandle || "", "");
});

test("fillSessionHandleFromAdapter is a no-op with null adapter", async () => {
  const args = { agentId: "a" };
  const out = await fillSessionHandleFromAdapter(args, null);
  assert.deepStrictEqual(out, args);
});

// ─── The llama-manager defect, 2026-08-19 ────────────────────────────────────
//
// A resident claude agent registered by hand, then showed "No pinned session handle yet"
// in the dashboard forever — re-registering never helped. Cause: this helper asked
// `getCurrentSessionId()`, which for claude reads CLAUDE_SESSION_ID, and CLAUDE CODE NEVER
// SETS THAT VARIABLE. The adapter's `discoverSessionId()` — capture store, then env, then
// the freshest transcript in the agent's OWN project dir — was never consulted on this path,
// though the session-handle heartbeat has consulted it since Plan 6.
//
// WHY DISCOVERY IS A FALLBACK HERE AND FIRST IN THE HEARTBEAT, which is a real asymmetry and
// not an oversight: the heartbeat is a long-lived correcting loop, so discover-first lets it
// walk away from a stale env value an operator left in a shell. Registration happens ONCE, and
// its env-read path is the behaviour four live runtimes are already tested against — so here
// discovery may only ADD a handle where there was none. It can never change a case that
// currently produces one.

test("fillSessionHandleFromAdapter discovers a claude handle when the env var is absent", async () => {
  delete process.env.CLAUDE_SESSION_ID;
  const dir = tmpDir("aify-register-discover-");
  fs.writeFileSync(
    path.join(dir, "aify-claude-session-llama-manager.json"),
    JSON.stringify({ sessionId: "d0147165-c9cd-42ab-9464-f5d147ca3ca8", agentId: "llama-manager" }),
  );
  try {
    const adapter = adapterFor("claude-code");
    const out = await fillSessionHandleFromAdapter(
      { agentId: "llama-manager" },
      adapter,
      { dir, homeDir: dir, cwd: path.join(dir, "nowhere") },
    );
    assert.strictEqual(out.sessionHandle, "d0147165-c9cd-42ab-9464-f5d147ca3ca8");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("fillSessionHandleFromAdapter discovers a claude handle from the agent's own transcript dir", async () => {
  // Path (c) of the adapter's precedence — the one that would have saved llama-manager,
  // whose capture store was empty because it launched without AIFY_AGENT_ID.
  delete process.env.CLAUDE_SESSION_ID;
  const home = tmpDir("aify-register-transcripts-");
  const workspace = "C:/Docker/aify-llamacpp-router";
  const projDir = path.join(home, ".claude", "projects", workspace.replace(/[^a-zA-Z0-9]/g, "-"));
  fs.mkdirSync(projDir, { recursive: true });
  fs.writeFileSync(path.join(projDir, "d0147165-c9cd-42ab-9464-f5d147ca3ca8.jsonl"), "{}\n");
  try {
    const adapter = adapterFor("claude-code");
    const out = await fillSessionHandleFromAdapter(
      { agentId: "llama-manager", cwd: workspace },
      adapter,
      { dir: home, homeDir: home },
    );
    assert.strictEqual(out.sessionHandle, "d0147165-c9cd-42ab-9464-f5d147ca3ca8");
  } finally {
    fs.rmSync(home, { recursive: true, force: true });
  }
});

test("a caller-supplied handle still wins over anything discoverable", async () => {
  delete process.env.CLAUDE_SESSION_ID;
  const dir = tmpDir("aify-register-precedence-");
  fs.writeFileSync(
    path.join(dir, "aify-claude-session-p.json"),
    JSON.stringify({ sessionId: "discovered-should-lose", agentId: "p" }),
  );
  try {
    const adapter = adapterFor("claude-code");
    const out = await fillSessionHandleFromAdapter(
      { agentId: "p", sessionHandle: "caller-wins" },
      adapter,
      { dir, homeDir: dir, cwd: path.join(dir, "nowhere") },
    );
    assert.strictEqual(out.sessionHandle, "caller-wins");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("the env handle still wins over anything discoverable", async () => {
  // Pins the fallback ordering. If discovery were promoted ahead of the env read, this goes red —
  // which is what stops a later "make it consistent with the heartbeat" edit from silently changing
  // registration for hermes, codex and pi as well.
  process.env.CLAUDE_SESSION_ID = "env-wins";
  const dir = tmpDir("aify-register-envfirst-");
  fs.writeFileSync(
    path.join(dir, "aify-claude-session-e.json"),
    JSON.stringify({ sessionId: "discovered-should-lose", agentId: "e" }),
  );
  try {
    const adapter = adapterFor("claude-code");
    const out = await fillSessionHandleFromAdapter(
      { agentId: "e" },
      adapter,
      { dir, homeDir: dir, cwd: path.join(dir, "nowhere") },
    );
    assert.strictEqual(out.sessionHandle, "env-wins");
  } finally {
    delete process.env.CLAUDE_SESSION_ID;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
