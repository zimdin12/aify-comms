#!/usr/bin/env node
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  buildPromptSubmitFrame,
  buildSessionSteerFrame,
  buildSessionMostRecentFrame,
  buildSessionListFrame,
  buildSessionInterruptFrame,
  buildAifySessionBindTransportFrame,
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

test("buildAifySessionBindTransportFrame targets visible-session binding", () => {
  const frame = buildAifySessionBindTransportFrame({ id: 10, sessionKey: "20260526_key" });
  assert.equal(frame.method, "aify.session.bind_transport");
  assert.deepEqual(frame.params, { session_id: "20260526_key" });
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
