// Reporting on agents, executed rather than scanned.
//
// `comms_agents` lists the fleet; `comms_agent_info` answers the same questions about one agent in more
// depth. They share four renderers and nothing else does, which is what makes them one group.
//
// THE LABELLING IS THE POINT, not the field list. Every inbound fact these report was individually TRUE
// during the 2026-08-10 outage while a reply sat undelivered, and a manager read them three times as
// evidence the lane was dead. "Unread: 0" is a statement about what an agent has not READ; it says nothing
// about whether the agent has produced anything. The outbound line exists to answer the question people
// were actually asking. A report that drops either label is not less informative — it is misleading, and it
// looks fine.

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import path from "node:path";

import { STDIO_DIR } from "./bridge-sources.mjs";

process.env.AIFY_SERVER_URL = "";
process.env.CLAUDE_MCP_SERVER_URL = "";

const reporting = await import("../agent-reporting-tools.mjs");
const { z } = await import("zod");

const tools = new Map();
reporting.registerAgentReportingTools(
  { tool: (name, description, schema, handler) => tools.set(name, { name, description, schema, handler }) },
  z,
);
const text = (res) => res.content[0].text;

test("the wrapper registers exactly the two reporting tools, and exports only itself", () => {
  assert.deepEqual([...tools.keys()].sort(), ["comms_agent_info", "comms_agents"]);
  assert.deepEqual(Object.keys(reporting).sort(), ["registerAgentReportingTools"]);
});

test("the module kept no state and reaches only owned leaves", () => {
  const src = readFileSync(path.join(STDIO_DIR, "agent-reporting-tools.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state belongs in a tool group");
  const imports = [...src.matchAll(/^import .* from "([^"]+)";$/gm)].map((m) => m[1]).sort();
  assert.deepEqual(imports, [
    // Sorted: "agent-" precedes "aify-" because 'g' < 'i'. Written the other way round first, which is a
    // reminder that a deepEqual against a hand-written sorted list tests my sorting as much as the code's.
    "./agent-summary.mjs", "./aify-service-endpoint.mjs", "./bound-agent-id.mjs",
    "./quote-subject.mjs", "./tool-response-format.mjs",
  ]);
});
