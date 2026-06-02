#!/usr/bin/env node
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  buildPromptSubmitFrame,
  buildSessionSteerFrame,
  buildSessionMostRecentFrame,
  buildSessionListFrame,
  buildSessionInterruptFrame,
  buildSessionActiveListFrame,
  pickSessionForKey,
  pickSessionStatusForKey,
  isGatewaySessionIdle,
  translateGatewayEvent,
  isSessionBusyError,
} from "../hermes-gateway-protocol.js";

test("buildPromptSubmitFrame is a JSON-RPC 2.0 prompt.submit", () => {
  const frame = buildPromptSubmitFrame({ id: 7, sessionId: "sess-1", text: "hi" });
  assert.equal(frame.jsonrpc, "2.0");
  assert.equal(frame.id, 7);
  assert.equal(frame.method, "prompt.submit");
  assert.deepEqual(frame.params, { session_id: "sess-1", text: "hi" });
});

test("buildPromptSubmitFrame coerces undefined sessionId/text to empty strings", () => {
  const frame = buildPromptSubmitFrame({ id: 1 });
  assert.deepEqual(frame.params, { session_id: "", text: "" });
});

test("buildSessionSteerFrame is a JSON-RPC 2.0 session.steer", () => {
  const frame = buildSessionSteerFrame({ id: 8, sessionId: "sess-1", text: "mid-run nudge" });
  assert.equal(frame.method, "session.steer");
  assert.deepEqual(frame.params, { session_id: "sess-1", text: "mid-run nudge" });
});

test("buildSessionMostRecentFrame is parameter-less", () => {
  const frame = buildSessionMostRecentFrame({ id: 1 });
  assert.equal(frame.method, "session.most_recent");
  assert.deepEqual(frame.params, {});
});

test("buildSessionListFrame is parameter-less", () => {
  const frame = buildSessionListFrame({ id: 2 });
  assert.equal(frame.method, "session.list");
  assert.deepEqual(frame.params, {});
});

test("buildSessionInterruptFrame targets a specific session", () => {
  const frame = buildSessionInterruptFrame({ id: 9, sessionId: "sess-1" });
  assert.equal(frame.method, "session.interrupt");
  assert.deepEqual(frame.params, { session_id: "sess-1" });
});

test("buildSessionActiveListFrame is a JSON-RPC 2.0 session.active_list", () => {
  const frame = buildSessionActiveListFrame({ id: 11, currentSessionId: "sid-9" });
  assert.equal(frame.jsonrpc, "2.0");
  assert.equal(frame.id, 11);
  assert.equal(frame.method, "session.active_list");
  assert.deepEqual(frame.params, { current_session_id: "sid-9" });
});

test("buildSessionActiveListFrame defaults current_session_id to empty string", () => {
  const frame = buildSessionActiveListFrame({ id: 12 });
  assert.deepEqual(frame.params, { current_session_id: "" });
});

test("pickSessionForKey: matches the row by stable session_key, returns live runtime id", () => {
  const resp = {
    result: {
      sessions: [
        { id: "ab12cd34", session_key: "aify-sc-hermes", status: "ready", started_at: "2026-05-31T10:00:00Z" },
        { id: "ff00ff00", session_key: "aify-other", status: "ready", started_at: "2026-05-31T11:00:00Z" },
      ],
    },
  };
  // Even though the OTHER session is fresher, the key match wins and we get the
  // EPHEMERAL runtime id (not the stable key).
  assert.equal(pickSessionForKey(resp, "aify-sc-hermes"), "ab12cd34");
});

test("pickSessionForKey: matches by exact runtime id when key is an id", () => {
  const resp = { sessions: [{ id: "ab12cd34", session_key: "aify-x" }] };
  assert.equal(pickSessionForKey(resp, "ab12cd34"), "ab12cd34");
});

test("pickSessionForKey: matches by title when no key/id match", () => {
  const resp = {
    result: {
      sessions: [{ id: "deadbeef", title: "aify-sc-hermes", status: "ready" }],
    },
  };
  assert.equal(pickSessionForKey(resp, "aify-sc-hermes"), "deadbeef");
});

test("pickSessionForKey: falls back to freshest by last_active when no match", () => {
  const resp = {
    result: {
      sessions: [
        { id: "old111", session_key: "k-old", last_active: "2026-05-31T09:00:00Z" },
        { id: "new222", session_key: "k-new", last_active: "2026-05-31T12:00:00Z" },
      ],
    },
  };
  assert.equal(pickSessionForKey(resp, "aify-nomatch"), "new222");
});

test("pickSessionForKey: returns null for empty/unknown response shapes", () => {
  assert.equal(pickSessionForKey({ result: { sessions: [] } }, "aify-x"), null);
  assert.equal(pickSessionForKey(null, "aify-x"), null);
  assert.equal(pickSessionForKey({}, "aify-x"), null);
});

// ---------------------------------------------------------------------------
// pickSessionStatusForKey + isGatewaySessionIdle — WS5 Task 5.2 turn-END signal.
// The gateway tracks session["running"] (True during a turn) and surfaces it as
// each session.active_list row's `status` (_session_live_status: running→"working",
// else "idle"; "starting"/"waiting" transitional). The managed-host turn-END
// signal observes this: status reads "idle" after a turn was submitted → the turn
// ended → POST /turn-end. This is the gateway's OWN process state, NOT the aify
// server's derived status (anti-feedback-loop safe).
// ---------------------------------------------------------------------------

test("pickSessionStatusForKey: returns the matched row's status by session_key", () => {
  const resp = {
    result: {
      sessions: [
        { id: "ab12", session_key: "aify-sc-hermes", status: "working" },
        { id: "cd34", session_key: "aify-other", status: "idle" },
      ],
    },
  };
  assert.equal(pickSessionStatusForKey(resp, "aify-sc-hermes"), "working");
});

test("pickSessionStatusForKey: matches by title when no session_key match", () => {
  const resp = {
    result: { sessions: [{ id: "ab12", title: "aify-sc-hermes", status: "idle" }] },
  };
  assert.equal(pickSessionStatusForKey(resp, "aify-sc-hermes"), "idle");
});

test("pickSessionStatusForKey: returns '' when the key is not present", () => {
  const resp = { result: { sessions: [{ id: "ab12", session_key: "aify-other", status: "working" }] } };
  assert.equal(pickSessionStatusForKey(resp, "aify-sc-hermes"), "");
});

test("pickSessionStatusForKey: returns '' for empty/unknown response shapes", () => {
  assert.equal(pickSessionStatusForKey({ result: { sessions: [] } }, "aify-x"), "");
  assert.equal(pickSessionStatusForKey(null, "aify-x"), "");
  assert.equal(pickSessionStatusForKey({}, "aify-x"), "");
});

test("isGatewaySessionIdle: true ONLY for the terminal idle status", () => {
  assert.equal(isGatewaySessionIdle("idle"), true);
  assert.equal(isGatewaySessionIdle("IDLE"), true);
});

test("isGatewaySessionIdle: false for working/transitional/unknown (never end a live turn early)", () => {
  // working = mid-turn; starting/waiting = transitional (agent building / pending
  // approval) — NOT idle. An unknown/empty status must NOT be read as idle, or a
  // gateway hiccup would falsely end a live turn (the #172 under-show-working trap).
  for (const s of ["working", "starting", "waiting", "ready", "", undefined, null]) {
    assert.equal(isGatewaySessionIdle(s), false, `status ${JSON.stringify(s)} must not be idle`);
  }
});

test("translateGatewayEvent maps agent.message.delta to a delta event", () => {
  const out = translateGatewayEvent({ jsonrpc: "2.0", method: "agent.message.delta", params: { delta: "abc" } });
  assert.deepEqual(out, { kind: "delta", text: "abc" });
});

test("translateGatewayEvent maps real tui_gateway message.delta envelope", () => {
  const out = translateGatewayEvent({
    jsonrpc: "2.0",
    method: "event",
    params: { type: "message.delta", session_id: "sid", payload: { text: "abc" } },
  });
  assert.deepEqual(out, { kind: "delta", text: "abc" });
});

test("translateGatewayEvent maps agent.message.end to a final event with text", () => {
  const out = translateGatewayEvent({ jsonrpc: "2.0", method: "agent.message.end", params: { text: "done" } });
  assert.deepEqual(out, { kind: "final", text: "done", status: "", warning: "" });
});

test("translateGatewayEvent maps real tui_gateway message.complete envelope", () => {
  const out = translateGatewayEvent({
    jsonrpc: "2.0",
    method: "event",
    params: { type: "message.complete", session_id: "sid", payload: { text: "done", status: "complete" } },
  });
  assert.deepEqual(out, { kind: "final", text: "done", status: "complete", warning: "" });
});

test("translateGatewayEvent maps tool.started to a tool_started event", () => {
  const out = translateGatewayEvent({ jsonrpc: "2.0", method: "tool.started", params: { tool: "bash" } });
  assert.deepEqual(out, { kind: "tool_started", label: "bash" });
});

test("translateGatewayEvent maps real tui_gateway tool.start envelope", () => {
  const out = translateGatewayEvent({
    jsonrpc: "2.0",
    method: "event",
    params: { type: "tool.start", session_id: "sid", payload: { name: "bash" } },
  });
  assert.deepEqual(out, { kind: "tool_started", label: "bash" });
});

test("translateGatewayEvent maps tool.completed to a tool_completed event", () => {
  const out = translateGatewayEvent({ jsonrpc: "2.0", method: "tool.completed", params: { name: "edit" } });
  assert.deepEqual(out, { kind: "tool_completed", label: "edit" });
});

test("translateGatewayEvent maps real tui_gateway tool.complete envelope", () => {
  const out = translateGatewayEvent({
    jsonrpc: "2.0",
    method: "event",
    params: { type: "tool.complete", session_id: "sid", payload: { name: "edit" } },
  });
  assert.deepEqual(out, { kind: "tool_completed", label: "edit" });
});

test("translateGatewayEvent maps error to an error event", () => {
  const out = translateGatewayEvent({ jsonrpc: "2.0", method: "error", params: { message: "boom" } });
  assert.deepEqual(out, { kind: "error", text: "boom" });
});

test("translateGatewayEvent maps real tui_gateway error envelope", () => {
  const out = translateGatewayEvent({
    jsonrpc: "2.0",
    method: "event",
    params: { type: "error", session_id: "sid", payload: { message: "boom" } },
  });
  assert.deepEqual(out, { kind: "error", text: "boom" });
});

test("translateGatewayEvent returns null for unknown methods (e.g. gateway.ready)", () => {
  assert.equal(translateGatewayEvent({ jsonrpc: "2.0", method: "event", params: { type: "gateway.ready" } }), null);
  assert.equal(translateGatewayEvent({ jsonrpc: "2.0", method: "telemetry.something", params: {} }), null);
});

test("isSessionBusyError recognizes hermes 4009 by code", () => {
  assert.equal(isSessionBusyError({ code: 4009, message: "session busy" }), true);
  assert.equal(isSessionBusyError({ code: 4009 }), true);
});

test("isSessionBusyError recognizes by message text when code is absent", () => {
  assert.equal(isSessionBusyError({ message: "Session busy, retry later" }), true);
});

test("isSessionBusyError rejects unrelated errors", () => {
  assert.equal(isSessionBusyError({ code: 5000, message: "policy denied" }), false);
  assert.equal(isSessionBusyError(null), false);
  assert.equal(isSessionBusyError(undefined), false);
});
