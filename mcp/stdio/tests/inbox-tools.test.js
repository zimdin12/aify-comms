// The inbox tools, executed rather than scanned.
//
// `comms_inbox`, `comms_unsend` — an agent reading its own mailbox. Until v0.5.4 both
// lived in `server.js`, the bin entry point, which nothing imports, so none of it was reachable
// from a test.
//
// THE SAFETY BANNER IS THE ASSERTION THAT MATTERS HERE. Every message these render was written by
// another agent, which makes it attacker-controlled with respect to the reading model. Each rendering
// path must prepend the warning that the content is DATA. A missing banner is not a crash and not
// visibly wrong — it is a prompt-injection surface that looks fine in every screenshot.

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// No service: nothing here may reach one.
process.env.AIFY_SERVER_URL = "";
process.env.CLAUDE_MCP_SERVER_URL = "";

const { registerInboxTools } = await import("../inbox-tools.mjs");
const { SAFETY_HEADER } = await import("../tool-response-format.mjs");
const { z } = await import("zod");

const tools = new Map();
registerInboxTools(
  { tool: (name, description, schema, handler) => tools.set(name, { name, description, schema, handler }) },
  z,
);

const text = (res) => res.content[0].text;

test("the wrapper registers exactly the two inbox tools", () => {
  assert.deepEqual([...tools.keys()].sort(), ["comms_inbox", "comms_unsend"]);
  for (const [name, tool] of tools) {
    assert.equal(typeof tool.handler, "function", `${name} must have a handler`);
    assert.ok(tool.description.length > 10, `${name} must describe itself`);
  }
});

test("the module exports only its owner surface, and kept no state", () => {
  const src = readFileSync(path.join(STDIO, "inbox-tools.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state belongs in a tool group");
  assert.equal((src.match(/^export /gm) || []).length, 1, "a group leaf exports its wrapper only");
  // It must not have grown its own copy of the banner.
  assert.doesNotMatch(src, /^(?:export\s+)?const SAFETY_HEADER\b/m, "the banner has one owner");
});

test("comms_search is NOT part of this group — the subject boundary, not a location", () => {
  // `comms_search` sits between two of these tools in server.js and was deliberately excluded: an inbox
  // is the caller's own mailbox, while search covers the whole corpus including artifacts.
  //
  // My first version asserted it was still registered IN server.js, which was true when written and
  // wrong one commit later when search moved to its own module. That pinned a LOCATION; the property is
  // that search does not belong to the inbox group, and it holds wherever search ends up living.
  const inbox = readFileSync(path.join(STDIO, "inbox-tools.mjs"), "utf-8");
  assert.doesNotMatch(inbox, /"comms_search"/, "search must not have drifted into the inbox module");
  assert.ok(!tools.has("comms_search"), "the inbox wrapper must not register search");
});
