#!/usr/bin/env node
// Unit tests for the hermes RuntimeAdapter (native-session-id model).
//
// Native-session-id model (2026-06-03 hermes-native-session-ids plan, Task 4):
// discoverSessionId returns the agent's OWN REAL hermes session id — resolved
// from the TUI active-session file, then HERMES_SESSION_ID env, then the
// per-agent session-id marker — NEVER the synthetic `aify-<agentId>` name. The
// retired pinnedSessionId path is gone; hermes is now symmetric with claude
// (captured UUID) / codex (resume thread).

import assert from "node:assert/strict";
import { test } from "node:test";
import os from "node:os";
import path from "node:path";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { HermesAdapter } from "../adapters/hermes.js";
import { writeSessionIdMarker, clearGatewayMarkers } from "../hermes-endpoint.js";

test("discoverSessionId reads the real id from the TUI active-session file", async () => {
  const adapter = new HermesAdapter();
  const dir = mkdtempSync(path.join(os.tmpdir(), "aify-hermes-adapter-"));
  const file = path.join(dir, "active.json");
  try {
    writeFileSync(file, JSON.stringify({ session_id: "20260603_120000_abc123" }));
    const id = await adapter.discoverSessionId({
      env: { AIFY_HERMES_ACTIVE_SESSION_FILE: file },
    });
    assert.equal(id, "20260603_120000_abc123");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("discoverSessionId accepts a bare (non-JSON) active-session file", async () => {
  const adapter = new HermesAdapter();
  const dir = mkdtempSync(path.join(os.tmpdir(), "aify-hermes-adapter-"));
  const file = path.join(dir, "active.txt");
  try {
    writeFileSync(file, "7afed304\n");
    const id = await adapter.discoverSessionId({
      env: { AIFY_HERMES_ACTIVE_SESSION_FILE: file },
    });
    assert.equal(id, "7afed304");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("discoverSessionId falls back to HERMES_SESSION_ID env (the real visible id)", async () => {
  const adapter = new HermesAdapter();
  // No active-session file → durable env handle wins. getCurrentSessionId reads
  // process.env, so set it there for this assertion.
  const prev = process.env.HERMES_SESSION_ID;
  try {
    process.env.HERMES_SESSION_ID = "20260601_real_env";
    const id = await adapter.discoverSessionId({ env: {} });
    assert.equal(id, "20260601_real_env");
  } finally {
    if (prev === undefined) delete process.env.HERMES_SESSION_ID;
    else process.env.HERMES_SESSION_ID = prev;
  }
});

test("discoverSessionId falls back to the per-agent session-id marker", async () => {
  const adapter = new HermesAdapter();
  const prevSid = process.env.HERMES_SESSION_ID;
  const prevSess = process.env.HERMES_SESSION;
  try {
    delete process.env.HERMES_SESSION_ID;
    delete process.env.HERMES_SESSION;
    writeSessionIdMarker("marker-agent", "20260603_marker_bound");
    const id = await adapter.discoverSessionId({ agentId: "marker-agent", env: {} });
    assert.equal(id, "20260603_marker_bound");
  } finally {
    clearGatewayMarkers("marker-agent");
    if (prevSid === undefined) delete process.env.HERMES_SESSION_ID;
    else process.env.HERMES_SESSION_ID = prevSid;
    if (prevSess === undefined) delete process.env.HERMES_SESSION;
    else process.env.HERMES_SESSION = prevSess;
  }
});

test("discoverSessionId never returns a synthetic aify-<id> name", async () => {
  const adapter = new HermesAdapter();
  const prevSid = process.env.HERMES_SESSION_ID;
  const prevSess = process.env.HERMES_SESSION;
  try {
    delete process.env.HERMES_SESSION_ID;
    delete process.env.HERMES_SESSION;
    const id = await adapter.discoverSessionId({
      agentId: "sc-coder",
      env: { AIFY_HERMES_GATEWAY_URL: "ws://127.0.0.1:9999" },
    });
    // No active file, no env session, no marker → falsy, and NEVER `aify-...`.
    assert.ok(!id || !/^aify-/.test(String(id)), `must not be a synthetic name, got ${id}`);
  } finally {
    if (prevSid === undefined) delete process.env.HERMES_SESSION_ID;
    else process.env.HERMES_SESSION_ID = prevSid;
    if (prevSess === undefined) delete process.env.HERMES_SESSION;
    else process.env.HERMES_SESSION = prevSess;
  }
});

test("discoverSessionId returns null when nothing is resolvable", async () => {
  const adapter = new HermesAdapter();
  const prevSid = process.env.HERMES_SESSION_ID;
  const prevSess = process.env.HERMES_SESSION;
  try {
    delete process.env.HERMES_SESSION_ID;
    delete process.env.HERMES_SESSION;
    const id = await adapter.discoverSessionId({ env: {} });
    assert.equal(id, null);
  } finally {
    if (prevSid === undefined) delete process.env.HERMES_SESSION_ID;
    else process.env.HERMES_SESSION_ID = prevSid;
    if (prevSess === undefined) delete process.env.HERMES_SESSION;
    else process.env.HERMES_SESSION = prevSess;
  }
});

test("capabilities: managed + interrupt on", () => {
  const adapter = new HermesAdapter();
  assert.equal(adapter.supportsInterrupt, true);
  assert.equal(adapter.supportsManaged, true);
});

test("sessionIdSource is 'captured' (the real visible session id)", () => {
  const adapter = new HermesAdapter();
  assert.equal(adapter.sessionIdSource, "captured");
});

test("resumeCommand returns the operator TUI takeover command", () => {
  const adapter = new HermesAdapter();
  assert.equal(
    adapter.resumeCommand("20260603_real_id"),
    "hermes --tui --resume 20260603_real_id",
  );
});
