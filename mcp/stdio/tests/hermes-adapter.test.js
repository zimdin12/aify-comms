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
import { tmpDir } from "./_tmpdir.js";

test("discoverSessionId reads the real id from the TUI active-session file", async () => {
  const adapter = new HermesAdapter();
  const dir = tmpDir("aify-hermes-adapter-");
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
  const dir = tmpDir("aify-hermes-adapter-");
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
  const id = await adapter.discoverSessionId({
    env: { HERMES_SESSION_ID: "20260601_real_env" },
  });
  assert.equal(id, "20260601_real_env");
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

test("discoverSessionId: explicit operator --resume id WINS over a stale marker (BUG 2)", async () => {
  const adapter = new HermesAdapter();
  const prevSid = process.env.HERMES_SESSION_ID;
  const prevSess = process.env.HERMES_SESSION;
  try {
    delete process.env.HERMES_SESSION_ID;
    delete process.env.HERMES_SESSION;
    // The marker holds a STALE id (the live symptom: registered handle was the
    // stale marker, not the operator-resumed session).
    writeSessionIdMarker("explicit-agent", "20260603_034413_8480e3");
    // The wrapper exports AIFY_EXPLICIT_SESSION_HANDLE=true + AIFY_SESSION_HANDLE
    // = the operator's --resume id. That MUST win over the marker.
    const id = await adapter.discoverSessionId({
      agentId: "explicit-agent",
      env: {
        AIFY_EXPLICIT_SESSION_HANDLE: "true",
        AIFY_SESSION_HANDLE: "20260529_071302_ea65af",
      },
    });
    assert.equal(id, "20260529_071302_ea65af", "explicit operator resume id is authoritative over the stale marker");
  } finally {
    clearGatewayMarkers("explicit-agent");
    if (prevSid === undefined) delete process.env.HERMES_SESSION_ID;
    else process.env.HERMES_SESSION_ID = prevSid;
    if (prevSess === undefined) delete process.env.HERMES_SESSION;
    else process.env.HERMES_SESSION = prevSess;
  }
});

test("discoverSessionId: a SEEDED active-session file still leads over the explicit env (BUG 2)", async () => {
  const adapter = new HermesAdapter();
  const dir = tmpDir("hermes-explicit-active-");
  const file = path.join(dir, "active.json");
  try {
    // The wrapper's resolve-session --explicit seeds BOTH the active file and the
    // marker with the operator id. The active-file is primary and matches the env.
    writeFileSync(file, JSON.stringify({ session_id: "20260529_071302_ea65af" }));
    const id = await adapter.discoverSessionId({
      agentId: "explicit-agent2",
      env: {
        AIFY_HERMES_ACTIVE_SESSION_FILE: file,
        AIFY_EXPLICIT_SESSION_HANDLE: "true",
        AIFY_SESSION_HANDLE: "20260529_071302_ea65af",
      },
    });
    assert.equal(id, "20260529_071302_ea65af", "seeded active file resolves the explicit id (primary)");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("discoverSessionId: NO explicit flag → stale marker still used (no regression)", async () => {
  const adapter = new HermesAdapter();
  const prevSid = process.env.HERMES_SESSION_ID;
  const prevSess = process.env.HERMES_SESSION;
  try {
    delete process.env.HERMES_SESSION_ID;
    delete process.env.HERMES_SESSION;
    writeSessionIdMarker("noexplicit-agent", "20260603_marker_bound");
    // AIFY_SESSION_HANDLE present but the explicit flag is NOT "true" → the
    // handle must NOT pre-empt the marker (it's just an inherited handle, not an
    // operator --resume). Marker continues to win as before.
    const id = await adapter.discoverSessionId({
      agentId: "noexplicit-agent",
      env: { AIFY_SESSION_HANDLE: "should-not-win" },
    });
    assert.equal(id, "20260603_marker_bound", "without the explicit flag the marker is still the fallback");
  } finally {
    clearGatewayMarkers("noexplicit-agent");
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
