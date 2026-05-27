#!/usr/bin/env node
// Unit tests for the hermes-acp-protocol module. Wire format details
// confirmed by docs/plans/notes/2026-05-23-hermes-acp-spike.md:
//   - newline-delimited JSON
//   - method names slash-separated (session/new, session/update, ...)
//   - field names camelCase (sessionId, mcpServers, stopReason, ...)
//   - session/update discriminator: `sessionUpdate` (camelCase key) whose
//     value is snake_case (agent_message_chunk, agent_thought_chunk, ...).

import assert from "node:assert/strict";
import {
  formatSessionUpdateAsTerminalFrame,
  encodeRequest,
  encodeNotification,
  encodeResponse,
  encodeError,
  parseMessage,
  METHODS,
} from "../hermes-acp-protocol.js";

// --- frame translation -----------------------------------------------------

// agent_message_chunk → raw text passthrough (chunks are concatenated by sink)
{
  const frame = formatSessionUpdateAsTerminalFrame({
    sessionUpdate: "agent_message_chunk",
    content: { type: "text", text: "hello world" },
  });
  assert.equal(frame, "hello world");
}

// agent_thought_chunk → ANSI dim+italic wrapper
{
  const frame = formatSessionUpdateAsTerminalFrame({
    sessionUpdate: "agent_thought_chunk",
    content: { type: "text", text: "thinking..." },
  });
  assert.match(frame, /thinking\.\.\./);
  assert.ok(frame.includes("\x1b["), "thought chunks must be ANSI-colorized");
}

// tool_call → yellow arrow + tool name + brief input
{
  const frame = formatSessionUpdateAsTerminalFrame({
    sessionUpdate: "tool_call",
    toolCallId: "tc-1",
    title: "read_file",
    kind: "read",
    rawInput: { path: "README.md" },
  });
  assert.match(frame, /read_file/);
  assert.match(frame, /README\.md/);
  assert.ok(frame.includes("\x1b["), "tool_call must be colorized");
}

// tool_call_update with status=completed → green check
{
  const frame = formatSessionUpdateAsTerminalFrame({
    sessionUpdate: "tool_call_update",
    toolCallId: "tc-1",
    title: "read_file",
    status: "completed",
    rawOutput: { length: 1234 },
  });
  assert.match(frame, /read_file/);
}

// tool_call_update with status=in_progress → empty (no noise)
{
  const frame = formatSessionUpdateAsTerminalFrame({
    sessionUpdate: "tool_call_update",
    status: "in_progress",
  });
  assert.equal(frame, "");
}

// usage_update → drops to empty (observed live; we don't want to spam the console)
{
  const frame = formatSessionUpdateAsTerminalFrame({
    sessionUpdate: "usage_update",
    size: 272000,
    used: 16603,
  });
  assert.equal(frame, "");
}

// available_commands_update → drops to empty
{
  const frame = formatSessionUpdateAsTerminalFrame({
    sessionUpdate: "available_commands_update",
    availableCommands: [{ name: "help" }],
  });
  assert.equal(frame, "");
}

// unknown variant → empty (graceful)
{
  const frame = formatSessionUpdateAsTerminalFrame({ sessionUpdate: "unknown_kind" });
  assert.equal(frame, "");
}

// undefined / null update → empty (defensive)
{
  assert.equal(formatSessionUpdateAsTerminalFrame(undefined), "");
  assert.equal(formatSessionUpdateAsTerminalFrame(null), "");
  assert.equal(formatSessionUpdateAsTerminalFrame({}), "");
}

// --- encoding --------------------------------------------------------------

// encodeRequest → newline-delimited JSON-RPC with camelCase params
{
  const wire = encodeRequest(7, METHODS.SESSION_PROMPT, {
    sessionId: "s",
    prompt: [{ type: "text", text: "hi" }],
  });
  assert.ok(wire.endsWith("\n"));
  const parsed = JSON.parse(wire.trim());
  assert.equal(parsed.jsonrpc, "2.0");
  assert.equal(parsed.id, 7);
  assert.equal(parsed.method, "session/prompt");
  assert.equal(parsed.params.sessionId, "s");
}

// encodeNotification → no id field
{
  const wire = encodeNotification("session/update", { sessionId: "s", update: {} });
  const parsed = JSON.parse(wire.trim());
  assert.equal(parsed.jsonrpc, "2.0");
  assert.equal(parsed.method, "session/update");
  assert.ok(!("id" in parsed), "notifications must not include an id");
}

// encodeResponse → result wrapped
{
  const wire = encodeResponse(3, { stopReason: "end_turn" });
  const parsed = JSON.parse(wire.trim());
  assert.equal(parsed.id, 3);
  assert.deepEqual(parsed.result, { stopReason: "end_turn" });
}

// encodeError → error code+message
{
  const wire = encodeError(4, -32601, "method not found");
  const parsed = JSON.parse(wire.trim());
  assert.equal(parsed.id, 4);
  assert.equal(parsed.error.code, -32601);
  assert.equal(parsed.error.message, "method not found");
}

// METHODS constant covers the slash-separated names we depend on
{
  assert.equal(METHODS.INITIALIZE, "initialize");
  assert.equal(METHODS.SESSION_NEW, "session/new");
  assert.equal(METHODS.SESSION_PROMPT, "session/prompt");
  assert.equal(METHODS.SESSION_CANCEL, "session/cancel");
  assert.equal(METHODS.SESSION_CLOSE, "session/close");
  assert.equal(METHODS.SESSION_UPDATE, "session/update");
  assert.equal(METHODS.FS_READ_TEXT_FILE, "fs/read_text_file");
  assert.equal(METHODS.FS_WRITE_TEXT_FILE, "fs/write_text_file");
  assert.equal(METHODS.SESSION_REQUEST_PERMISSION, "session/request_permission");
}

// --- parseMessage ----------------------------------------------------------

// Two complete lines → two messages, empty remainder
{
  const buf = '{"jsonrpc":"2.0","id":1,"result":{}}\n{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"s","update":{}}}\n';
  const { messages, remainder } = parseMessage(buf);
  assert.equal(messages.length, 2);
  assert.equal(remainder, "");
  assert.equal(messages[1].method, "session/update");
}

// Partial trailing line → returned as remainder
{
  const buf = '{"jsonrpc":"2.0","id":1,"result":{}}\n{"jsonrpc":"2.0","id":2,"par';
  const { messages, remainder } = parseMessage(buf);
  assert.equal(messages.length, 1);
  assert.equal(remainder, '{"jsonrpc":"2.0","id":2,"par');
}

// Empty input → empty arrays
{
  const { messages, remainder } = parseMessage("");
  assert.equal(messages.length, 0);
  assert.equal(remainder, "");
}

// Malformed line is dropped silently (so a partial bridge crash doesn't poison the stream)
{
  const buf = '{"jsonrpc":"2.0","id":1,"result":{}}\nnot-json-at-all\n{"jsonrpc":"2.0","id":2,"result":null}\n';
  const { messages, remainder } = parseMessage(buf);
  assert.equal(messages.length, 2);
  assert.equal(messages[0].id, 1);
  assert.equal(messages[1].id, 2);
}

console.log("hermes-acp-protocol.test.js: all assertions passed");
