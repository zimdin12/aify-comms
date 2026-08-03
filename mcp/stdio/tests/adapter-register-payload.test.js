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

test("fillSessionHandleFromAdapter preserves caller-supplied handle", () => {
  process.env.CLAUDE_SESSION_ID = "from-env";
  const adapter = adapterFor("claude-code");
  const args = { agentId: "a", sessionHandle: "caller-handle" };
  const out = fillSessionHandleFromAdapter(args, adapter);
  assert.strictEqual(out.sessionHandle, "caller-handle");
  delete process.env.CLAUDE_SESSION_ID;
});

test("fillSessionHandleFromAdapter fills empty sessionHandle from adapter env", () => {
  process.env.CLAUDE_SESSION_ID = "from-env";
  const adapter = adapterFor("claude-code");
  const args = { agentId: "a" };
  const out = fillSessionHandleFromAdapter(args, adapter);
  assert.strictEqual(out.sessionHandle, "from-env");
  delete process.env.CLAUDE_SESSION_ID;
});

test("fillSessionHandleFromAdapter fills Hermes handle from the REAL env session id", () => {
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
    const out = fillSessionHandleFromAdapter(args, adapter);
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

test("fillSessionHandleFromAdapter fills Hermes handle from the live TUI active-session file", () => {
  const dir = tmpDir("aify-register-hermes-");
  const activeFile = path.join(dir, "active.json");
  fs.writeFileSync(activeFile, JSON.stringify({ session_id: "20260715_live_plain_tui" }));
  process.env.AIFY_HERMES_ACTIVE_SESSION_FILE = activeFile;
  delete process.env.HERMES_SESSION_ID;
  try {
    const adapter = adapterFor("hermes");
    const out = fillSessionHandleFromAdapter({ agentId: "plain-hermes" }, adapter);
    assert.strictEqual(out.sessionHandle, "20260715_live_plain_tui");
  } finally {
    delete process.env.AIFY_HERMES_ACTIVE_SESSION_FILE;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("fillSessionHandleFromAdapter preserves a caller-supplied Hermes handle", () => {
  process.env.HERMES_SESSION_ID = "env-session-should-not-override";
  process.env.AIFY_HERMES_GATEWAY_URL = "ws://127.0.0.1:9999/api/ws?token=x";
  const adapter = adapterFor("hermes");
  const args = { agentId: "h", sessionHandle: "caller-supplied-real-id" };
  const out = fillSessionHandleFromAdapter(args, adapter);
  assert.strictEqual(out.sessionHandle, "caller-supplied-real-id");
  delete process.env.HERMES_SESSION_ID;
  delete process.env.AIFY_HERMES_GATEWAY_URL;
});

test("fillSessionHandleFromAdapter leaves empty when env has no handle", () => {
  delete process.env.CLAUDE_SESSION_ID;
  const adapter = adapterFor("claude-code");
  const args = { agentId: "a" };
  const out = fillSessionHandleFromAdapter(args, adapter);
  assert.strictEqual(out.sessionHandle || "", "");
});

test("fillSessionHandleFromAdapter is a no-op with null adapter", () => {
  const args = { agentId: "a" };
  const out = fillSessionHandleFromAdapter(args, null);
  assert.deepStrictEqual(out, args);
});
