#!/usr/bin/env node
// comms_run_interrupt on a run its target never claimed: the service cancels it and answers status "cancelled"
// (2026-10-13). The tool says so, instead of announcing a control request the service never made.

import assert from "node:assert/strict";
import http from "node:http";

let answer = {};
const server = http.createServer((req, res) => {
  res.writeHead(200, { "content-type": "application/json" });
  res.end(JSON.stringify(answer));
});
await new Promise((resolve) => server.listen(0, "127.0.0.2", resolve));
const { port } = server.address();

process.env.AIFY_SERVER_URL = `http://127.0.0.2:${port}`;
process.env.CLAUDE_MCP_SERVER_URL = "";

const { registerDispatchTools } = await import("../dispatch-tools.mjs");
const { z } = await import("zod");
const tools = new Map();
registerDispatchTools({ tool: (name, description, schema, handler) => tools.set(name, { handler }) }, z);
const interrupt = tools.get("comms_run_interrupt").handler;

try {
  answer = { ok: true, runId: "run-q", action: "interrupt", status: "cancelled" };
  const cancelled = (await interrupt({ runId: "run-q" })).content[0].text;
  assert.match(cancelled, /run-q was still queued, so it was cancelled before delivery/);
  assert.doesNotMatch(cancelled, /Control ID/);

  answer = { ok: true, runId: "run-r", action: "interrupt", status: "pending", controlId: "ctl-1" };
  const pending = (await interrupt({ runId: "run-r" })).content[0].text;
  assert.match(pending, /Interrupt requested for run-r\. Control ID: ctl-1/, "CONTROL: a running run still reports its control");
} finally {
  server.close();
}
console.log("comms-run-interrupt-reports-a-cancel.test.js: all assertions passed");
