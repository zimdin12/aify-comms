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
let payload = { contracts: [], summary: { total: 0 } };
const server = http.createServer((req, res) => {
  received.push(req.url);
  res.writeHead(200, { "content-type": "application/json" });
  res.end(JSON.stringify(payload));
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

// ── How a contract READS, which is the other half of this tool ───────────────
//
// `summarizeContract` renders each line. It is private to the dispatch module — the reviewer's rule is
// that a group-exclusive helper stays inside the group and is proven through the handler that calls it,
// not promoted to a module export so a test can reach it. `comms_contracts` is that handler, so these
// assertions go through it. Nothing else in the bridge renders a contract.

// `rows`, not `contracts` — the outer `contracts` is the registered tool, and shadowing it here would
// make the handler call below read as recursion.
async function render(rows) {
  payload = { contracts: rows, summary: { total: rows.length } };
  const res = await contracts.handler({});
  return res.content[0].text;
}

const line = await render([{
  from: "agent-a", targetAgentId: "agent-b", state: "missing_reply",
  subject: "deploy the thing", ageMinutes: 42,
}]);
assert.match(line, /MISSING REPLY/, "an underscore state must read as words, upper-cased");
assert.match(line, /agent-a -> agent-b/, "a contract must name both ends");
assert.match(line, /42m/, "an age under an hour is shown in minutes");
assert.match(line, /deploy the thing/);

// The hour boundary, from both sides. 60 minutes is already hours.
assert.match(await render([{ ageMinutes: 90 }]), /1\.5h/);
assert.match(await render([{ ageMinutes: 60 }]), /1h/);
assert.match(await render([{ ageMinutes: 59 }]), /59m/);

// Degenerate inputs. This output is read by a human deciding what they owe; "undefined -> NaN" tells
// them nothing, and the service can legitimately omit any of these fields.
for (const contract of [{}, { ageMinutes: "" }, { ageMinutes: null }, { ageMinutes: "abc" }, { ageMinutes: -5 }]) {
  const out = await render([contract]);
  assert.ok(!/undefined|NaN|\[object Object\]/.test(out), `leaked a placeholder: ${out}`);
}
assert.match(await render([{}]), /\(no subject\)/, "a contract with no subject says so");
assert.match(await render([{}]), /SENT/, "state falls back to sent");

// An unbounded answer preview would flood the caller's context.
const long = await render([{ resultPreview: "x".repeat(500) }]);
assert.match(long, /answer: x+/);
assert.ok(long.length < 400, `the preview is not being truncated: ${long.length} chars`);

// And the empty case must say so rather than rendering a blank body.
assert.match(await render([]), /No matching contracts\./);

server.close();
console.log("comms-contracts-defaults.test.js: all assertions passed");
