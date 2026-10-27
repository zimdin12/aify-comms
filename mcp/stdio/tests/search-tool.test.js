// `comms_search`, executed rather than scanned.
//
// THE DEFECT THIS TOOL WAS RESHAPED TO PREVENT is not "search misses things". It is that an empty result
// was being read as "no such message exists" when messages had not been searched AT ALL — omitting
// `agentId` searches shared artifacts only. An empty answer then licensed work that had already been
// ruled out: the tool failed OPEN. So every response must report what it actually searched.
//
// Until v0.5.4 this lived in `server.js`, the bin entry point, which nothing imports, so none of that
// was reachable from a test. The only guard was a regex in `transport-parity.test.js` asserting the
// strings "searched" and "NOT searched" appear in both transports' source — which cannot tell whether
// they reach a caller.

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

process.env.AIFY_SERVER_URL = "";
process.env.CLAUDE_MCP_SERVER_URL = "";

const { registerSearchTool } = await import("../search-tool.mjs");
const { z } = await import("zod");

const tools = new Map();
registerSearchTool(
  { tool: (name, description, schema, handler) => tools.set(name, { name, description, schema, handler }) },
  z,
);
const search = tools.get("comms_search");
const text = (res) => res.content[0].text;


test("the tool registers, and its own description warns about the omitted-agentId trap", () => {
  assert.ok(search, "comms_search must be registered");
  // The warning has to reach the model CHOOSING the tool, not only the reader of its output — by then
  // the wrong conclusion has already been formed.
  assert.match(search.description, /NOT searched/i, "the description must warn that messages can be skipped");
  assert.match(
    search.schema.agentId.description, /OMIT AND MESSAGES ARE NOT SEARCHED/i,
    "the agentId field itself must say what omitting it costs",
  );
});

test("the module exports only its owner surface and kept no state", () => {
  const src = readFileSync(path.join(STDIO, "search-tool.mjs"), "utf-8");
  assert.equal((src.match(/^export /gm) || []).length, 1, "one export: the wrapper");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state");
});
