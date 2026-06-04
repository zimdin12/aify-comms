#!/usr/bin/env node
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  buildPromptSubmitFrame,
  buildSessionSteerFrame,
  buildRenderNoticeFrame,
  buildSessionMostRecentFrame,
  buildSessionListFrame,
  buildSessionInterruptFrame,
  buildSessionActiveListFrame,
  pickSessionForKey,
  pickSessionById,
  pickMostRecentSession,
  pickSessionRowById,
  pickMostRecentSessionRow,
  rowResumeKey,
  pickSessionStatusForKey,
  pickSessionStatusById,
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

test("buildRenderNoticeFrame is a JSON-RPC 2.0 aify.session.render_notice", () => {
  const frame = buildRenderNoticeFrame({
    id: 9,
    sessionId: "sess-1",
    notice: "Incoming from bob\n\nhello",
    status: "aify-comms · bob",
  });
  assert.equal(frame.jsonrpc, "2.0");
  assert.equal(frame.id, 9);
  assert.equal(frame.method, "aify.session.render_notice");
  assert.deepEqual(frame.params, {
    session_id: "sess-1",
    notice: "Incoming from bob\n\nhello",
    status: "aify-comms · bob",
  });
});

test("buildRenderNoticeFrame coerces missing fields to empty strings", () => {
  const frame = buildRenderNoticeFrame({ id: 1, sessionId: "s" });
  assert.deepEqual(frame.params, { session_id: "s", notice: "", status: "" });
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
// Native-session-id resolvers (2026-06-03 Task 3): pickSessionById +
// pickMostRecentSession. The native-id delivery loop targets the agent's OWN
// real session id (resumed at launch / captured at register), not aify-<id>.
// ---------------------------------------------------------------------------

test("pickSessionById: matches a row by its REAL session id (id / session_id / sessionId)", () => {
  const resp = {
    result: {
      sessions: [
        { id: "20260603_aaa", status: "idle" },
        { session_id: "20260603_bbb", status: "working" },
        { sessionId: "20260603_ccc", status: "idle" },
      ],
    },
  };
  assert.equal(pickSessionById(resp, "20260603_aaa"), "20260603_aaa");
  assert.equal(pickSessionById(resp, "20260603_bbb"), "20260603_bbb");
  assert.equal(pickSessionById(resp, "20260603_ccc"), "20260603_ccc");
});

test("pickSessionById: null when the id is not live / empty / unknown shape", () => {
  const resp = { result: { sessions: [{ id: "real-1" }] } };
  assert.equal(pickSessionById(resp, "not-there"), null);
  assert.equal(pickSessionById(resp, ""), null);
  assert.equal(pickSessionById(null, "real-1"), null);
  assert.equal(pickSessionById({ result: { sessions: [] } }, "real-1"), null);
});

test("pickMostRecentSession: returns the freshest live session's real id (the visible TUI)", () => {
  const resp = {
    result: {
      sessions: [
        { id: "old", started_at: "2026-06-03T09:00:00Z" },
        { id: "newest", started_at: "2026-06-03T12:00:00Z" },
        { id: "mid", started_at: "2026-06-03T10:00:00Z" },
      ],
    },
  };
  assert.equal(pickMostRecentSession(resp), "newest");
});

test("pickMostRecentSession: falls back to the first row with an id when no timestamps", () => {
  const resp = { result: { sessions: [{ id: "first" }, { id: "second" }] } };
  assert.equal(pickMostRecentSession(resp), "first");
});

test("pickMostRecentSession: null when there are no live sessions / unknown shape", () => {
  assert.equal(pickMostRecentSession({ result: { sessions: [] } }), null);
  assert.equal(pickMostRecentSession(null), null);
  assert.equal(pickMostRecentSession({}), null);
});

// ---------------------------------------------------------------------------
// rowResumeKey + row-returning resolvers (2026-06-04 session_key fix). The
// resume/marker path must persist the DURABLE `session_key`, not the ephemeral
// runtime `id`, or the next launch resumes a dead sid → gateway 4007.
// ---------------------------------------------------------------------------

test("rowResumeKey: returns the durable session_key over the ephemeral id", () => {
  assert.equal(
    rowResumeKey({ id: "8b821120", session_key: "20260604_215845_395891" }),
    "20260604_215845_395891",
    "prefers the durable session_key",
  );
  assert.equal(
    rowResumeKey({ id: "8b821120", sessionKey: "20260604_215845_395891" }),
    "20260604_215845_395891",
    "accepts the camelCase sessionKey too",
  );
});

test("rowResumeKey: falls back to the ephemeral id when no session_key is present", () => {
  assert.equal(rowResumeKey({ id: "8b821120" }), "8b821120", "graceful degradation");
  assert.equal(rowResumeKey({ session_id: "abc" }), "abc");
  assert.equal(rowResumeKey({}), "");
  assert.equal(rowResumeKey(null), "");
});

test("pickSessionRowById: matches by ephemeral id OR durable session_key, returns the row", () => {
  const resp = {
    result: {
      sessions: [
        { id: "8b821120", session_key: "20260604_215845_395891", status: "idle" },
        { id: "other", session_key: "20260604_000000_000000", status: "idle" },
      ],
    },
  };
  // marker holds the ephemeral id → resolves the row whose durable key differs
  assert.equal(rowResumeKey(pickSessionRowById(resp, "8b821120")), "20260604_215845_395891");
  // marker holds the durable key directly → still resolves the row
  assert.equal(rowResumeKey(pickSessionRowById(resp, "20260604_215845_395891")), "20260604_215845_395891");
  assert.equal(pickSessionRowById(resp, "not-there"), null);
  assert.equal(pickSessionRowById(null, "x"), null);
});

test("pickMostRecentSessionRow: returns the freshest row so the caller can take its session_key", () => {
  const resp = {
    result: {
      sessions: [
        { id: "old-sid", session_key: "20260603_090000_old", started_at: "2026-06-03T09:00:00Z" },
        { id: "new-sid", session_key: "20260603_120000_new", started_at: "2026-06-03T12:00:00Z" },
      ],
    },
  };
  assert.equal(rowResumeKey(pickMostRecentSessionRow(resp)), "20260603_120000_new");
  assert.equal(pickMostRecentSessionRow({ result: { sessions: [] } }), null);
});

test("pickSessionStatusById: reads the live status for a row matched by its real id", () => {
  const resp = {
    result: {
      sessions: [
        { id: "real-1", status: "working" },
        { id: "real-2", status: "idle" },
      ],
    },
  };
  assert.equal(pickSessionStatusById(resp, "real-1"), "working");
  assert.equal(pickSessionStatusById(resp, "real-2"), "idle");
  assert.equal(pickSessionStatusById(resp, "absent"), "", "absent id → '' (treated as not-idle)");
  assert.equal(pickSessionStatusById(resp, ""), "");
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
