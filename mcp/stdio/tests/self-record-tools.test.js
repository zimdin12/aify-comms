// An agent writing its own record, executed rather than scanned.
//
// `comms_status` records what an agent is currently working on; `comms_describe` records what it exists to
// do. The exact complement of `agent-reporting-tools.mjs` — those READ about agents, these WRITE the
// caller's own row.
//
// A STATUS WRITTEN HERE IS A SELF-REPORT, not the derived status the service computes from events, and it
// cannot be: an agent that has hung cannot update a field to say so. That distinction is the reason the two
// live in different places, and the reason nothing deciding liveness may read this one.

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import path from "node:path";

import { STDIO_DIR } from "./bridge-sources.mjs";

process.env.AIFY_SERVER_URL = "";
process.env.CLAUDE_MCP_SERVER_URL = "";

const selfRecord = await import("../self-record-tools.mjs");
const { z } = await import("zod");

const tools = new Map();
selfRecord.registerSelfRecordTools(
  { tool: (name, description, schema, handler) => tools.set(name, { name, description, schema, handler }) },
  z,
);
const text = (res) => res.content[0].text;
const seed = () => writeAgents({ agents: { "agent-a": { role: "coder", runtime: "codex", status: "idle" } } });

test("the wrapper registers exactly the two self-record tools and exports only itself and its words", () => {
  assert.deepEqual([...tools.keys()].sort(), ["comms_describe", "comms_status"]);
  // SELF_REPORTED_STATUSES (D12) is a frozen word list, exported so vocabulary-agreement.test.js holds it to the contract.
  assert.deepEqual(Object.keys(selfRecord).sort(), ["SELF_REPORTED_STATUSES", "registerSelfRecordTools"]);
  assert.ok(Object.isFrozen(selfRecord.SELF_REPORTED_STATUSES));
});

test("the module kept no state and reaches only owned leaves", () => {
  const src = readFileSync(path.join(STDIO_DIR, "self-record-tools.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state belongs in a tool group");
  const imports = [...src.matchAll(/^import .* from "([^"]+)";$/gm)].map((m) => m[1]).sort();
  assert.deepEqual(imports, ["./aify-service-endpoint.mjs", "./safe-name.mjs"]);
});
