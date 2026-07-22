#!/usr/bin/env node
// Verifies comms_console_tail / comms_console_input call the right endpoints
// with the right args. httpCall is injected so no real server is needed.
import assert from "node:assert/strict";

// IS_REMOTE is derived from a server URL env var at import time; set one so the
// handlers take the remote path.
process.env.AIFY_SERVER_URL = process.env.AIFY_SERVER_URL || "http://127.0.0.1:8800";
process.env.AIFY_AGENT_ID = "manager-bot";

const {
  commsConsoleTailHandler,
  commsConsoleInputHandler,
  commsInterruptHandler,
  CONSOLE_INPUT_TOOL_DESCRIPTION,
  COMMS_SEND_TOOL_DESCRIPTION,
} = await import("../server.js");

assert.match(CONSOLE_INPUT_TOOL_DESCRIPTION, /recovery-only/i);
assert.match(CONSOLE_INPUT_TOOL_DESCRIPTION, /read the console first/i);
assert.match(CONSOLE_INPUT_TOOL_DESCRIPTION, /do not inject normal work messages/i);

// --- comms_interrupt: target the agent's live console with terminal-native Ctrl+C ---
{
  const calls = [];
  const fakeHttp = async (method, endpoint, body) => {
    calls.push({ method, endpoint, body });
    return { ok: true, live: true, terminalId: "term_live", controlId: "ctl_interrupt" };
  };
  const res = await commsInterruptHandler(
    { agentId: "busy-agent", from: "manager-bot" },
    { httpCall: fakeHttp },
  );
  assert.deepEqual(calls, [{
    method: "POST",
    endpoint: "/agents/busy-agent/console/input",
    body: { text: "\u0003", enter: false, from: "manager-bot" },
  }]);
  assert.match(res.content[0].text, /Interrupted busy-agent/);
}

assert.match(COMMS_SEND_TOOL_DESCRIPTION, /omit requireReply/i,
  "normal type defaults should not require a reply override");
assert.match(COMMS_SEND_TOOL_DESCRIPTION, /set requireReply=true/i,
  "the exceptional opt-in must be explicit");
assert.match(COMMS_SEND_TOOL_DESCRIPTION, /set requireReply=false/i,
  "the intentional fire-and-forget override must be explicit");

// --- comms_console_tail: GET the console endpoint with capped lines ---
{
  const calls = [];
  const fakeHttp = async (method, endpoint) => {
    calls.push({ method, endpoint });
    return { ok: true, live: true, terminalId: "term_x", status: "running", lines: 3, output: "a\nb\nc" };
  };
  const res = await commsConsoleTailHandler({ agentId: "stuck-agent", lines: 3 }, { httpCall: fakeHttp });
  assert.equal(calls.length, 1, "tail should make exactly one call");
  assert.equal(calls[0].method, "GET");
  assert.equal(calls[0].endpoint, "/agents/stuck-agent/console?lines=3");
  assert.match(res.content[0].text, /last 3 lines/);
  assert.match(res.content[0].text, /a\nb\nc/);
}

// --- comms_console_tail: clamps lines to the 1..200 range ---
{
  const calls = [];
  const fakeHttp = async (method, endpoint) => {
    calls.push({ method, endpoint });
    return { ok: true, live: true, terminalId: "t", status: "running", lines: 200, output: "x" };
  };
  await commsConsoleTailHandler({ agentId: "a", lines: 9999 }, { httpCall: fakeHttp });
  assert.equal(calls[0].endpoint, "/agents/a/console?lines=200", "lines must be clamped to 200");
}

// --- comms_console_tail: live:false surfaces the server message ---
{
  const fakeHttp = async () => ({ ok: true, live: false, message: "a has no live console (it lazy-starts on a message)." });
  const res = await commsConsoleTailHandler({ agentId: "a" }, { httpCall: fakeHttp });
  assert.equal(res.isError, undefined, "live:false is not an error");
  assert.match(res.content[0].text, /no live console/);
}

// --- comms_console_input: POST the input endpoint with text/enter/from ---
{
  const calls = [];
  const fakeHttp = async (method, endpoint, body) => {
    calls.push({ method, endpoint, body });
    return { ok: true, live: true, terminalId: "term_y", controlId: "ctl_1" };
  };
  const res = await commsConsoleInputHandler(
    { agentId: "stuck-agent", text: "/status", enter: true, from: "manager-bot" },
    { httpCall: fakeHttp }
  );
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "POST");
  assert.equal(calls[0].endpoint, "/agents/stuck-agent/console/input");
  assert.deepEqual(calls[0].body, { text: "/status", enter: true, from: "manager-bot" });
  assert.match(res.content[0].text, /Input sent to stuck-agent/);
}

// --- comms_console_input: defaults enter=true and from=AIFY_AGENT_ID ---
{
  const calls = [];
  const fakeHttp = async (method, endpoint, body) => {
    calls.push({ method, endpoint, body });
    return { ok: true, live: true, terminalId: "t", controlId: "c" };
  };
  await commsConsoleInputHandler({ agentId: "a" }, { httpCall: fakeHttp });
  assert.equal(calls[0].body.enter, true, "enter defaults true");
  assert.equal(calls[0].body.from, "manager-bot", "from defaults to AIFY_AGENT_ID");
  assert.equal(calls[0].body.text, "", "text defaults to empty string");
}

// --- comms_console_input: ok:false from server becomes an error result ---
{
  const fakeHttp = async () => ({ ok: false, live: false, message: "a has no live console; send a message to start it first." });
  const res = await commsConsoleInputHandler({ agentId: "a" }, { httpCall: fakeHttp });
  assert.equal(res.isError, true);
  assert.match(res.content[0].text, /no live console/);
}

console.log("console-tools.test.js: all assertions passed");
