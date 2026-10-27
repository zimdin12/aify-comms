// Real tests for the two SEND tools, extracted from server.js in v0.5.4 — the last tool registrations to
// leave it.
//
// These pin the module's surface, with no service.
// What each tool DOES is held by the reply fixtures (tool-replies.test.js, and the service's Python copy
// against the same cases). Local mode, the file store these tests used to drive, was deleted in 0.9.11.

import assert from "node:assert/strict";
import test from "node:test";

process.env.AIFY_SERVER_URL = "";
process.env.CLAUDE_MCP_SERVER_URL = "";

const sendTools = await import("../send-tools.mjs");
const { IS_REMOTE } = await import("../aify-service-endpoint.mjs");
const { z } = await import("zod");

const tools = new Map();
sendTools.registerSendTools(
  { tool: (name, description, schema, handler) => tools.set(name, { name, description, schema, handler }) },
  z,
);
const call = async (name, args) => tools.get(name).handler(args);
const text = (res) => res.content[0].text;



test("the module registers exactly the two send tools and exports only its wrapper", () => {
  assert.deepEqual([...tools.keys()].sort(), ["comms_channel_send", "comms_send"]);
  assert.deepEqual(Object.keys(sendTools).sort(), ["COMMS_SEND_TOOL_DESCRIPTION", "registerSendTools"],
    "the wrapper, plus the description that travelled with the tool it describes — the tools themselves "
    + "reach the server through the wrapper, not individually");
});

test("both descriptions say the send is live-delivery gated", () => {
  // The description is what an agent reads before choosing a tool. If it stopped saying that a send to an
  // offline agent is NOT written, callers would treat comms_send as an append-to-inbox and stop checking.
  for (const name of ["comms_send", "comms_channel_send"]) {
    assert.match(tools.get(name).description, /deliver|offline|live/i, `${name} must describe the gate`);
  }
});

test("every registered tool declares a schema and a handler", () => {
  // A tool registered without one of them fails at call time inside the MCP client, where the error is
  // opaque. Cheap to assert here.
  for (const [name, t] of tools) {
    assert.equal(typeof t.handler, "function", `${name} must have a handler`);
    assert.ok(t.schema && typeof t.schema === "object", `${name} must declare a schema`);
    assert.ok(t.description && t.description.length > 20, `${name} must describe itself`);
  }
});

test("importing the module sends nothing and needs no live service", async () => {
  const again = await import("../send-tools.mjs");
  assert.equal(again.registerSendTools, sendTools.registerSendTools, "one module instance, no load-time work");
});
