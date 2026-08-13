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
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { STDIO_DIR, declaringModules, toolSources } from "./bridge-sources.mjs";

const STORE = mkdtempSync(path.join(os.tmpdir(), "aify-agent-reporting-"));
process.env.AIFY_SERVER_URL = "";
process.env.CLAUDE_MCP_SERVER_URL = "";
process.env.CLAUDE_MCP_MESSAGES_DIR = STORE;

const reporting = await import("../agent-reporting-tools.mjs");
const { readAgents, writeAgents, deliverMessage, readInbox, markAsRead, MESSAGES_DIR } = await import("../local-store.mjs");
const { z } = await import("zod");

const tools = new Map();
reporting.registerAgentReportingTools(
  { tool: (name, description, schema, handler) => tools.set(name, { name, description, schema, handler }) },
  z,
);
const text = (res) => res.content[0].text;

test("the scratch store is really in use", () => {
  assert.ok(MESSAGES_DIR.startsWith(STORE), `expected the scratch store, got ${MESSAGES_DIR}`);
});

test("the wrapper registers exactly the two reporting tools, and exports only itself", () => {
  assert.deepEqual([...tools.keys()].sort(), ["comms_agent_info", "comms_agents"]);
  assert.deepEqual(Object.keys(reporting).sort(), ["registerAgentReportingTools"]);
});

test("a registered agent is listed with its runtime, wake path and mode", async () => {
  writeAgents({
    agents: {
      "agent-a": {
        role: "coder", runtime: "codex", sessionMode: "managed",
        capabilities: ["managed-run"], machineId: "box-1", lastSeen: "2026-08-13T00:00:00Z",
      },
    },
  });
  const listed = text(await tools.get("comms_agents").handler({}));
  assert.match(listed, /agent-a/, "the agent must be listed");
  assert.match(listed, /codex/, "its runtime must be named");
  assert.match(listed, /managed-worker/, "its WAKE PATH must be named — this is how an operator knows it is reachable");
});

test("comms_agent_info labels its inbound facts AS inbound", async () => {
  // The assertion this file exists for. Each of these numbers can be true while a reply sits undelivered,
  // and an unlabelled "Unread: 0" was read three times as proof a lane was dead.
  writeAgents({ agents: { "agent-b": { role: "tester", runtime: "pi", sessionMode: "resident", lastSeen: "x" } } });
  const info = text(await tools.get("comms_agent_info").handler({ agentId: "agent-b" }));
  assert.match(info, /Unread/, "the unread count must be reported");
  assert.match(info, /Runtime:/, "…and the runtime");
  assert.match(info, /Wake mode:/, "…and the wake path, which is the deliverability answer");
  assert.ok(!/undefined|NaN|\[object Object\]/.test(info), `leaked a placeholder: ${info}`);
});

test("BOTH of comms_agent_info's branches report the wake path — local and remote", () => {
  // The behavioural assertion above only exercises LOCAL mode, because IS_REMOTE is fixed at module load.
  // Deleting the `Wake mode:` line from the REMOTE branch therefore changed nothing and the test stayed
  // green — a mutation caught that, not review.
  //
  // The remote branch needs a service to execute, which this file has no harness for. So the property is
  // asserted structurally instead, and stated as such: the tool has two renderings and BOTH must carry the
  // wake path, because an operator reading a remote deployment gets the other one.
  const src = readFileSync(path.join(STDIO_DIR, "agent-reporting-tools.mjs"), "utf-8");
  const info = src.slice(src.indexOf('"comms_agent_info"'));
  assert.equal(
    (info.match(/Wake mode:/g) || []).length, 2,
    "both the remote and local renderings of comms_agent_info must report the wake path",
  );
  assert.equal((info.match(/Runtime:/g) || []).length, 2, "…and both must report the runtime");
  // Outbound activity is remote-only by nature — the local store has no record of it — so exactly one.
  assert.equal(
    (info.match(/formatOutboundActivity\(/g) || []).length, 1,
    "outbound activity is a remote-only fact; if this becomes 2 the local branch is claiming data it lacks",
  );
});

test("the unread count counts UNREAD messages, not all messages", async () => {
  // A count that included read messages would make an idle agent look backed up, and a manager would go
  // looking for work that had already been done.
  //
  // THIS TEST WAS VACUOUS FIRST TIME. It delivered ONE message, left it unread, and asserted "Unread: 1" —
  // which is what `readInbox(id, "all")` returns too. Switching the filter to "all" changed nothing and the
  // test stayed green. Distinguishing the two requires a READ message to exist, so that the counts differ.
  writeAgents({ agents: { "agent-c": { role: "coder", runtime: "codex", sessionMode: "managed" } } });
  assert.match(text(await tools.get("comms_agent_info").handler({ agentId: "agent-c" })), /Unread: 0/,
    "a fresh agent owes nothing");

  deliverMessage("agent-c", { id: "m1", from: "agent-a", subject: "one", body: "b" });
  deliverMessage("agent-c", { id: "m2", from: "agent-a", subject: "two", body: "b" });
  markAsRead("agent-c", readInbox("agent-c", "unread").slice(0, 1));

  const after = text(await tools.get("comms_agent_info").handler({ agentId: "agent-c" }));
  assert.equal(readInbox("agent-c", "all").length, 2, "two messages exist…");
  assert.equal(readInbox("agent-c", "unread").length, 1, "…of which one is unread");
  assert.match(after, /Unread: 1/, "the report must show the UNREAD count, not the total of 2");
});

test("an unknown agent is reported as not found, not as an empty agent", async () => {
  // The dangerous alternative is a rendered record full of blanks, which reads as "exists but idle".
  const res = await tools.get("comms_agent_info").handler({ agentId: "never-registered" });
  assert.equal(res.isError, true, "an unknown agent is an error, not an empty report");
  assert.match(text(res), /not found/i);
});

test("an empty fleet says so rather than rendering nothing", async () => {
  writeAgents({ agents: {} });
  const listed = text(await tools.get("comms_agents").handler({}));
  assert.equal(typeof listed, "string");
  assert.ok(listed.trim().length > 0, "an empty fleet must still produce a readable answer");
  assert.ok(!/undefined|\[object Object\]/.test(listed), `leaked a placeholder: ${listed}`);
  assert.equal(Object.keys(readAgents().agents).length, 0, "…and the store really was empty");
});

test("the four shared renderers have exactly one owner each, wherever their callers live", () => {
  // The group's founding property. Asserted through `bridge-sources.mjs` rather than against a filename:
  // seven assertions in this lane broke by naming server.js, and two of them were younger than the commit
  // that broke them.
  const owners = {
    runtimeSummary: "agent-summary.mjs",
    wakeModeSummary: "agent-summary.mjs",
    formatDispatchState: "tool-response-format.mjs",
    formatOutboundActivity: "tool-response-format.mjs",
  };
  for (const [name, file] of Object.entries(owners)) {
    assert.deepEqual(
      declaringModules(name), [{ file, kind: "function" }],
      `${name} must be declared exactly once, by ${file}`,
    );
  }
});

test("the two tools are registered exactly once across the whole bridge", () => {
  // The failure a per-file check cannot see: a tool left behind in server.js AND added to a group module
  // registers twice, and which handler wins depends on registration order.
  for (const name of ["comms_agents", "comms_agent_info"]) {
    const registering = toolSources().filter(([, src]) =>
      new RegExp(`server\\.tool\\(\\s*\\n?\\s*"${name}"`).test(src));
    assert.equal(registering.length, 1, `${name} is registered by ${registering.map(([f]) => f).join(", ")}`);
    assert.equal(registering[0][0], "agent-reporting-tools.mjs");
  }
});

test("the module kept no state and reaches only owned leaves", () => {
  const src = readFileSync(path.join(STDIO_DIR, "agent-reporting-tools.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state belongs in a tool group");
  const imports = [...src.matchAll(/^import .* from "([^"]+)";$/gm)].map((m) => m[1]).sort();
  assert.deepEqual(imports, [
    // Sorted: "agent-" precedes "aify-" because 'g' < 'i'. Written the other way round first, which is a
    // reminder that a deepEqual against a hand-written sorted list tests my sorting as much as the code's.
    "./agent-summary.mjs", "./aify-service-endpoint.mjs", "./local-store.mjs", "./tool-response-format.mjs",
  ]);
});

process.on("exit", () => { try { rmSync(STORE, { recursive: true, force: true }); } catch { /* best effort */ } });
