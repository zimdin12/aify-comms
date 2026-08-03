// A destructive MCP tool must SAY it is destructive, in its description.
//
// The description is the only thing a model reads before deciding to call a tool. Reviewed
// 2026-08-03 across all 32 comms_* tools, the warning budget was inverted: comms_restart — which
// is recoverable — carried 692 characters, while comms_clear, which permanently wipes every
// message, artifact and agent identity on the shared hub with no undo and no confirmation, carried
// 73: "Clear messages, shared files, agents, or everything. Optional age filter."
//
// Nothing in that sentence tells a model it is about to destroy other teams' history. This test
// pins the floor: name the destruction, and name the narrower tool to reach for instead.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "server.js"), "utf8");

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// Description text for a tool = everything between its name and its zod schema block.
function describeTool(name) {
  const at = source.indexOf(`"${name}",`);
  assert.notEqual(at, -1, `${name} must still be registered`);
  const rest = source.slice(at + name.length + 3);
  const end = rest.indexOf("\n  {");
  return rest.slice(0, end === -1 ? 400 : end);
}

const DESTRUCTIVE = ["comms_clear", "comms_remove_agent", "comms_compact"];

test("every destructive tool announces that it destroys something", () => {
  for (const name of DESTRUCTIVE) {
    assert.match(
      describeTool(name),
      /DESTRUCTIVE/,
      `${name} destroys data and must say so before a model decides to call it`,
    );
  }
});

test("comms_clear states the blast radius and that it cannot be undone", () => {
  const d = describeTool("comms_clear");
  assert.match(d, /IRREVERSIBLE|no undo/i, "must say it cannot be undone");
  assert.match(d, /whole hub|every message|other teams/i, "must say the scope is the shared hub, not the caller");
});

test("each destructive tool points at the narrower alternative", () => {
  // The common intent behind reaching for these is almost never the destructive one.
  assert.match(describeTool("comms_clear"), /comms_remove_agent/);
  assert.match(describeTool("comms_remove_agent"), /comms_restart/);
  assert.match(describeTool("comms_compact"), /durable|write/i);
});

let failed = 0;
for (const [name, fn] of tests) {
  try { fn(); console.log(`  ok   ${name}`); }
  catch (e) { failed += 1; console.log(`  FAIL ${name}\n       ${e.message}`); }
}
console.log(`\n${tests.length - failed}/${tests.length} destructive-tool-description tests passed`);
if (failed) process.exit(1);
