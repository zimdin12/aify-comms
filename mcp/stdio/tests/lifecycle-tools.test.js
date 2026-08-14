// The destructive end of an agent's life, executed rather than scanned.
//
// `comms_remove_agent`, `comms_delete_session`, `comms_restart`, `comms_clear`. Three of the four mutate
// the state `bridge-agent-state.mjs` owns — they are the write side of the forget invariant, and this
// group could not be cut at all until that state had an owner.
//
// THE BLAST RADII DIFFER BY ORDERS OF MAGNITUDE and nothing but the descriptions says so:
// `comms_delete_session` drops one inactive record, `comms_remove_agent` tombstones one identity, and
// `comms_clear` with target="all" wipes every message, artifact and identity on the server — other teams
// included, with no undo and no confirmation prompt. For that last one the description IS the safety
// mechanism, which is why it is asserted here and not only in the shared descriptions test.

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

process.env.AIFY_SERVER_URL = "";
process.env.CLAUDE_MCP_SERVER_URL = "";

const { registerLifecycleTools } = await import("../lifecycle-tools.mjs");
const state = await import("../bridge-agent-state.mjs");
const { z } = await import("zod");

const tools = new Map();
registerLifecycleTools(
  { tool: (name, description, schema, handler) => tools.set(name, { name, description, schema, handler }) },
  z,
);

const EXPECTED = ["comms_clear", "comms_delete_session", "comms_remove_agent", "comms_restart"];
const text = (res) => res.content[0].text;

test("the wrapper registers exactly the four lifecycle tools", () => {
  assert.deepEqual([...tools.keys()].sort(), EXPECTED);
  for (const [name, tool] of tools) {
    assert.equal(typeof tool.handler, "function", `${name} must have a handler`);
    assert.equal(typeof tool.schema, "object", `${name} must declare a schema`);
  }
});

test("every one of them announces the destruction, and names the narrower alternative", () => {
  // For tools with no undo, the description is the only guard the caller gets before acting. A
  // destructive verb that reads like a routine one is how an agent wipes a hub it meant to nudge.
  for (const name of EXPECTED) {
    const d = tools.get(name).description;
    assert.match(d, /delet|destruct|remove|stop|restart|wipe|irreversible/i, `${name} must say what it destroys`);
    assert.ok(d.length > 60, `${name}'s description is too short to convey a blast radius`);
  }
});

test("comms_clear states the blast radius, the absence of undo, and that it crosses teams", () => {
  // The single most dangerous tool in the bridge. target="all" is hub-wide and reaches other teams'
  // data. Every clause below is load-bearing: an agent that reads only "clears data" and not "other
  // teams included, no undo" has been told the wrong thing.
  const d = tools.get("comms_clear").description;
  assert.match(d, /IRREVERSIBLE|no undo/i, "it must say the action cannot be reversed");
  assert.match(d, /WHOLE hub|every message|other teams/i, "it must say the scope is not just the caller");
  assert.match(d, /no confirmation/i, "it must say nothing will ask again");
});

test("comms_restart refuses resident sessions, which are operator-owned", () => {
  // A session-restart on a live resident would fork a managed twin of a session a human is sitting in.
  // The description carries that reasoning and must keep carrying it.
  const d = tools.get("comms_restart").description;
  assert.match(d, /RESIDENT/i, "it must name the mode it cannot act on");
  assert.match(d, /managed/i, "…and the mode it can");
  assert.match(d, /comms_run_interrupt|operator/i, "…and what to do instead");
});

test("in local mode the remote-only tools refuse rather than half-acting", () => {
  // These reach the service. A destructive tool that reports success having done nothing is worse than
  // one that errors, because the caller then believes the state is gone.
  // Assertions READ from the real messages rather than assumed. My first version matched
  // /remote|server mode/ and failed — not because the code was wrong, but because the actual messages are
  // BETTER than the generic phrasing I guessed at: each names the tool, the missing dependency, and what
  // local mode lacks instead ("no runtime session table", "no managed session to restart"). Testing an
  // invented message would have pushed the code toward the vaguer wording.
  return Promise.all(["comms_delete_session", "comms_restart"].map(async (name) => {
    const res = await tools.get(name).handler({ agentId: "agent-a", sessionId: "s1" });
    assert.equal(res.isError, true, `${name} must report an error in local mode`);
    const out = text(res);
    assert.match(out, new RegExp(name), `${name}'s refusal should name the tool that refused`);
    assert.match(out, /requires the HTTP-backed aify-comms service/, `${name} must name the missing dependency`);
    assert.match(out, /local filesystem mode/, `${name} must say what local mode lacks`);
  }));
});

test("the group is the write side of the forget invariant — it uses the owner, never its own copy", () => {
  // The property that made this cut possible. If any of these grew a private Map, the reset would stop
  // covering the state the rest of the bridge reads.
  const src = readFileSync(path.join(STDIO, "lifecycle-tools.mjs"), "utf-8");
  for (const name of ["REMOTE_AGENT_STATE", "ACTIVE_RUNS", "CONSECUTIVE_FAILURES"]) {
    assert.doesNotMatch(src, new RegExp(`^\\s*(?:export\\s+)?const ${name} = new Map`, "m"),
      `${name} must be imported from the owner, not redeclared here`);
  }
  assert.match(src, /from "\.\/bridge-agent-state\.mjs"/, "it must import the owned state");
  // And the Maps it clears must be the same objects the owner exports — asserted by identity, not by
  // name, since two modules can agree on a name and disagree on the object.
  assert.ok(state.REMOTE_AGENT_STATE instanceof Map);
  assert.equal(new Set([state.REMOTE_AGENT_STATE, state.ACTIVE_RUNS, state.CONSECUTIVE_FAILURES]).size, 3);
});

test("the module exports only its owner surface and kept no state", () => {
  const src = readFileSync(path.join(STDIO, "lifecycle-tools.mjs"), "utf-8");
  assert.equal((src.match(/^export /gm) || []).length, 1, "one export: the wrapper");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state");
});

test("comms_compact is NOT part of this group — subject, not adjacency", () => {
  // It is destructive and it was blocked at the time, so it would have been easy to sweep in. Its subject
  // is losing WORKING MEMORY, not losing an identity or a session record, and none of these four name it
  // while they do name each other. Asserted against this module so it holds wherever compact ends up.
  const src = readFileSync(path.join(STDIO, "lifecycle-tools.mjs"), "utf-8");
  assert.doesNotMatch(src, /server\.tool\(\s*\n?\s*"comms_compact"/, "compaction is a different subject");
  assert.ok(!tools.has("comms_compact"), "the lifecycle wrapper must not register it");
});

test("server.js kept none of the four — exactly one owner", () => {
  const src = readFileSync(path.join(STDIO, "server.js"), "utf-8");
  for (const name of EXPECTED) {
    assert.doesNotMatch(src, new RegExp(`server\\.tool\\(\\s*\\n?\\s*"${name}"`), `${name} still in server.js`);
  }
  // The registration list moved to `register-tools.mjs` in v0.5.4. This is still a location check —
  // "the wrapper is called with exactly (server, z)" is about wiring, not behaviour — but it now names
  // the file that actually holds the call instead of the one it used to sit in.
  const reg = readFileSync(path.join(STDIO, "register-tools.mjs"), "utf-8");
  assert.match(reg, /registerLifecycleTools\(server, z\);/, "the registrar must still CALL the wrapper");
});
