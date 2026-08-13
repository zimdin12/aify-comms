#!/usr/bin/env node
// `comms_contracts` defaults to OPEN contracts in the DIRECT category.
//
// Both defaults are load-bearing. Without `state=open` the tool answers with every contract ever
// recorded, and an agent reading it cannot tell what it still owes. Without `category=direct`, old
// channel fan-out floods the list and buries the work that is actually assigned to the caller — which
// is the wording of the tool's own schema description.
//
// THIS TEST USED TO BE A REGEX OVER server.js SOURCE. It matched
// `params.set("state", state || "open")` as literal text, which meant it also asserted the file's
// indentation: when the tool moved into `dispatch-tools.mjs` (v0.5.4) and gained one level of nesting
// inside `registerDispatchTools`, the pin broke without a single behavioural change. A source pin is a
// consumer of formatting, and it can only ever prove that a line was WRITTEN.
//
// It is now the real thing: the real tool registered on a fake MCP server, the real handler invoked,
// and the query string it actually put on the wire read off a real loopback HTTP server. That proves
// the default REACHED THE SERVICE, which the regex never could.

import assert from "node:assert/strict";
import http from "node:http";

const received = [];
const server = http.createServer((req, res) => {
  received.push(req.url);
  res.writeHead(200, { "content-type": "application/json" });
  res.end(JSON.stringify({ contracts: [], summary: { total: 0 } }));
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const { port } = server.address();

// Set before the import: the endpoint leaf resolves its URL, and IS_REMOTE with it, at module load.
process.env.AIFY_SERVER_URL = `http://127.0.0.1:${port}`;
process.env.CLAUDE_MCP_SERVER_URL = "";

const { registerDispatchTools } = await import("../dispatch-tools.mjs");
const { z } = await import("zod");

// The smallest thing `server.tool(...)` will accept: it only ever records.
const tools = new Map();
registerDispatchTools(
  { tool: (name, description, schema, handler) => tools.set(name, { description, schema, handler }) },
  z,
);

const contracts = tools.get("comms_contracts");
assert.ok(contracts, "comms_contracts should be registered by registerDispatchTools");

function lastQuery() {
  const url = received.at(-1);
  assert.ok(url, "the handler did not call the service at all");
  return new URLSearchParams(url.slice(url.indexOf("?") + 1));
}

// ── The defaults, with nothing supplied ──────────────────────────────────────
await contracts.handler({});
assert.equal(lastQuery().get("state"), "open", "contracts must default to open contracts");
assert.equal(lastQuery().get("category"), "direct", "contracts must default to the direct category");

// ── An explicit value must still win, or the default is a lock, not a default ──
await contracts.handler({ state: "overdue", category: "channel" });
assert.equal(lastQuery().get("state"), "overdue", "an explicit state must override the default");
assert.equal(lastQuery().get("category"), "channel", "an explicit category must override the default");

// ── The schema still OFFERS the states the default selects between ───────────
assert.ok(contracts.schema.state, "state should be part of the tool schema");
// `.unwrap()` steps through the `.optional()` wrapper to the enum. Read from zod rather than assumed:
// the private `_def.innerType._def.values` shape I reached for first does not exist in this version,
// and a test written against an invented signature proves nothing about the code.
assert.deepEqual(
  contracts.schema.state.unwrap().options.slice(0, 3),
  ["open", "overdue", "working"],
  "the open/overdue/working states the default chooses between must remain in the enum",
);
assert.match(
  contracts.schema.category.description,
  /Defaults to direct/,
  "the direct default must stay documented — an agent cannot see a default it is not told about",
);

server.close();
console.log("comms-contracts-defaults.test.js: all assertions passed");
