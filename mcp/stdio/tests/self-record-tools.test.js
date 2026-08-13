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
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { STDIO_DIR, toolSources } from "./bridge-sources.mjs";

const STORE = mkdtempSync(path.join(os.tmpdir(), "aify-self-record-"));
process.env.AIFY_SERVER_URL = "";
process.env.CLAUDE_MCP_SERVER_URL = "";
process.env.CLAUDE_MCP_MESSAGES_DIR = STORE;

const selfRecord = await import("../self-record-tools.mjs");
const { readAgents, writeAgents, MESSAGES_DIR } = await import("../local-store.mjs");
const { z } = await import("zod");

const tools = new Map();
selfRecord.registerSelfRecordTools(
  { tool: (name, description, schema, handler) => tools.set(name, { name, description, schema, handler }) },
  z,
);
const text = (res) => res.content[0].text;
const seed = () => writeAgents({ agents: { "agent-a": { role: "coder", runtime: "codex", status: "idle" } } });

test("the scratch store is really in use", () => {
  assert.ok(MESSAGES_DIR.startsWith(STORE), `expected the scratch store, got ${MESSAGES_DIR}`);
});

test("the wrapper registers exactly the two self-record tools and exports only itself", () => {
  assert.deepEqual([...tools.keys()].sort(), ["comms_describe", "comms_status"]);
  assert.deepEqual(Object.keys(selfRecord).sort(), ["registerSelfRecordTools"]);
});

test("a status written by an agent lands in its own row and nobody else's", async () => {
  writeAgents({
    agents: {
      "agent-a": { role: "coder", status: "idle" },
      "agent-b": { role: "tester", status: "idle" },
    },
  });
  const res = await tools.get("comms_status").handler({ agentId: "agent-a", status: "working on the parser" });
  assert.ok(!res.isError, `status failed: ${text(res)}`);

  const after = readAgents().agents;
  assert.match(JSON.stringify(after["agent-a"]), /working on the parser/, "the caller's row must record it");
  assert.doesNotMatch(JSON.stringify(after["agent-b"]), /working on the parser/,
    "another agent's row must be untouched — this writes the whole registry back");
  assert.equal(after["agent-b"].role, "tester", "…and must not lose their other fields");
});

test("THE TWO ARE ASYMMETRIC: status works locally, describe is remote-only", async () => {
  // Found by writing this test, not by reading the code. I assumed two sibling tools that write adjacent
  // fields of the same row would support the same modes. They do not: `comms_status` has a local-filesystem
  // branch and `comms_describe` refuses with "currently requires remote server mode".
  //
  // Whether that asymmetry should exist is a behavioural question and this is a structural slice, so it is
  // pinned here. Note the word "currently" in the refusal — whoever wrote it expected to come back. Worth
  // knowing before someone assumes the pair behave alike, which is exactly the assumption I made.
  seed();
  const status = await tools.get("comms_status").handler({ agentId: "agent-a", status: "working on the parser" });
  assert.ok(!status.isError, `status should work in local mode: ${text(status)}`);

  const describe = await tools.get("comms_describe").handler({ agentId: "agent-a", description: "owns the parser" });
  assert.equal(describe.isError, true, "describe has no local branch");
  assert.match(text(describe), /requires remote server mode/i, "…and says so rather than silently no-op'ing");
  assert.doesNotMatch(
    JSON.stringify(readAgents().agents["agent-a"]), /owns the parser/,
    "a refused describe must not have written anything",
  );
});

test("a local status write patches the row rather than rebuilding it", async () => {
  // The registry is read, modified and written back WHOLE, so a handler that rebuilt the record instead of
  // patching it would silently drop every other field. Nothing else would notice.
  seed();
  await tools.get("comms_status").handler({ agentId: "agent-a", status: "working on the parser" });
  const row = readAgents().agents["agent-a"];
  assert.match(JSON.stringify(row), /working on the parser/, "the new status must be there");
  assert.equal(row.role, "coder", "…and the pre-existing role must survive");
  assert.equal(row.runtime, "codex", "…and the runtime");
  // It also stamps lastSeen, which is how a self-report doubles as a liveness signal for the local store.
  assert.ok(row.lastSeen, "a status write should stamp when it happened");
});

test("the NAME GUARD refuses a traversal-shaped id even when a matching row exists", async () => {
  // THIS TEST WAS VACUOUS FIRST TIME. It passed traversal ids against an ordinary registry and got
  // `isError` for all of them — but removing the `validateName` guard entirely changed nothing, because an
  // id like "../escape" is also not a registered agent, so the unknown-agent check refused it anyway.
  // Every rejection I was crediting to the name guard came from somewhere else.
  //
  // Seeding a row under that exact key removes the other explanation: the row EXISTS, so only the name
  // guard can still refuse it. That is also the case that matters — a store which somehow contains a
  // badly-named key must not become writable through it.
  writeAgents({
    agents: {
      "agent-a": { role: "coder" },
      "../escape": { role: "smuggled" },
      "a/b": { role: "smuggled" },
    },
  });
  const before = JSON.stringify(readAgents());
  for (const agentId of ["../escape", "a/b"]) {
    const res = await tools.get("comms_status").handler({ agentId, status: "should not land" });
    assert.equal(res.isError, true, `comms_status must reject ${agentId} on its NAME, not its absence`);
    assert.match(text(res), /Invalid/i, "…and the refusal must come from the name guard");
  }
  assert.equal(JSON.stringify(readAgents()), before, "a rejected write must not have touched the store");
});

test("an unregistered agent cannot write a record for itself", async () => {
  // Otherwise a typo creates a phantom row that shows up in the fleet listing as a real agent.
  writeAgents({ agents: {} });
  for (const name of ["comms_status", "comms_describe"]) {
    const res = await tools.get(name).handler({ agentId: "never-registered", status: "x", description: "x" });
    assert.equal(res.isError, true, `${name} must not create a row for an unknown agent`);
  }
  assert.deepEqual(Object.keys(readAgents().agents), [], "no phantom row may appear");
});

test("each tool is registered exactly once across the whole bridge", () => {
  for (const name of ["comms_status", "comms_describe"]) {
    const registering = toolSources().filter(([, src]) =>
      new RegExp(`server\\.tool\\(\\s*\\n?\\s*"${name}"`).test(src));
    assert.equal(registering.length, 1, `${name} registered by ${registering.map(([f]) => f).join(", ")}`);
    assert.equal(registering[0][0], "self-record-tools.mjs");
  }
});

test("the module kept no state and reaches only owned leaves", () => {
  const src = readFileSync(path.join(STDIO_DIR, "self-record-tools.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state belongs in a tool group");
  const imports = [...src.matchAll(/^import .* from "([^"]+)";$/gm)].map((m) => m[1]).sort();
  assert.deepEqual(imports, ["./aify-service-endpoint.mjs", "./local-store.mjs", "./safe-name.mjs"]);
});

process.on("exit", () => { try { rmSync(STORE, { recursive: true, force: true }); } catch { /* best effort */ } });
