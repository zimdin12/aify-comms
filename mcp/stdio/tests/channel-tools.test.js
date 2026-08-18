// Channel membership and reading, executed rather than scanned.
//
// `comms_channel_create`, `comms_channel_join`, `comms_channel_read`, `comms_channel_list`. In local mode a
// channel is ONE JSON FILE, `channels/<name>.json`, so "does this channel exist" is a single-file question —
// and a missing file must not be reported as an empty channel. That absence-versus-emptiness
// distinction has bitten this repo twice already, in `comms_search`'s scope note and in `aify-comms doctor`'s
// `unknown-all`.
//
// `comms_channel_send` is deliberately absent from this group; see the module header. These tests assert that
// absence as a property, because the group is incomplete BY RECORD and a later edit should not quietly
// complete it before its dependency has an owner.

import assert from "node:assert/strict";
import test from "node:test";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { STDIO_DIR, isUsedInBridge, toolSources } from "./bridge-sources.mjs";

const STORE = mkdtempSync(path.join(os.tmpdir(), "aify-channels-"));
process.env.AIFY_SERVER_URL = "";
process.env.CLAUDE_MCP_SERVER_URL = "";
process.env.CLAUDE_MCP_MESSAGES_DIR = STORE;

const channels = await import("../channel-tools.mjs");
const { MESSAGES_DIR } = await import("../local-store.mjs");
const { SAFETY_HEADER } = await import("../tool-response-format.mjs");
const { z } = await import("zod");

const tools = new Map();
channels.registerChannelTools(
  { tool: (name, description, schema, handler) => tools.set(name, { name, description, schema, handler }) },
  z,
);
const text = (res) => res.content[0].text;
const call = (name, args) => tools.get(name).handler(args);

test("the scratch store is really in use", () => {
  assert.ok(MESSAGES_DIR.startsWith(STORE), `expected the scratch store, got ${MESSAGES_DIR}`);
});

test("the wrapper registers exactly the five channel tools and exports only itself", () => {
  // `comms_channel_delete` joined them 2026-08-18. It is the most destructive delete an agent can
  // reach — channel, membership and every message ever posted, for every member — so the endpoint
  // gained a creator-or-operator check in the same change. Membership is deliberately not enough:
  // to stop receiving a channel you LEAVE it.
  assert.deepEqual(
    [...tools.keys()].sort(),
    ["comms_channel_create", "comms_channel_delete", "comms_channel_join", "comms_channel_list",
     "comms_channel_read"],
  );
  assert.deepEqual(Object.keys(channels).sort(), ["registerChannelTools"]);
});

test("a created channel exists on disk and appears in the listing", async () => {
  const res = await call("comms_channel_create", { name: "build-team", from: "agent-a" });
  assert.ok(!res.isError, `create failed: ${text(res)}`);
  assert.match(text(await call("comms_channel_list", {})), /build-team/, "a created channel must be listed");
});

test("creating the same channel twice is not an error and does not duplicate it", async () => {
  // Agents retry. A second create that failed would make a benign retry look like a problem; one that
  // duplicated the channel would split a team's messages across two directories with the same name.
  await call("comms_channel_create", { name: "twice", from: "agent-a" });
  const second = await call("comms_channel_create", { name: "twice", from: "agent-a" });
  assert.ok(!second.isError, `a repeat create must be tolerated: ${text(second)}`);
  const listed = text(await call("comms_channel_list", {}));
  assert.equal((listed.match(/twice/g) || []).length, 1, "the channel must appear exactly once");
});

test("joining records membership, and joining twice does not duplicate it", async () => {
  await call("comms_channel_create", { name: "joinable", from: "agent-a" });
  const res = await call("comms_channel_join", { channel: "joinable", from: "agent-a", agentId: "agent-b" });
  assert.ok(!res.isError, `join failed: ${text(res)}`);

  await call("comms_channel_join", { channel: "joinable", from: "agent-a", agentId: "agent-b" });

  // A channel is ONE JSON FILE, `channels/<name>.json` — not a directory per channel, which is what I
  // assumed and asserted first. Read from the source rather than inferred from the message store's other
  // layouts (inboxes ARE a directory per agent, which is where the wrong guess came from).
  const chFile = path.join(MESSAGES_DIR, "channels", "joinable.json");
  assert.ok(existsSync(chFile), "the channel file must exist");
  const record = JSON.parse(readFileSync(chFile, "utf-8"));
  const members = JSON.stringify(record.members || record);
  assert.equal(
    (members.match(/agent-b/g) || []).length, 1,
    `a repeated join must not duplicate the member: ${members}`,
  );
  assert.match(members, /agent-a/, "the creator is auto-joined, per comms_channel_create's own description");
});

test("PINNED WART: create takes `name`, the others take `channel` — same concept, two spellings", () => {
  // Found by writing this file, not by reading the code. `comms_channel_create` names its parameter `name`
  // while join and read call the identical thing `channel`. Passing the wrong one is not rejected — it
  // arrives as undefined, and my first version of these tests created a channel literally called
  // "undefined" while every assertion still saw `isError` from a later guard and looked satisfied.
  //
  // Pinned rather than normalised: renaming a tool parameter is a breaking API change for every agent and
  // skill that calls it, which is behavioural and not this slice's business. Recorded so the next person
  // hits the assertion rather than the confusion.
  assert.ok(tools.get("comms_channel_create").schema.name, "create's parameter is `name`");
  assert.ok(!tools.get("comms_channel_create").schema.channel, "…and it is NOT `channel`");
  for (const tool of ["comms_channel_join", "comms_channel_read"]) {
    assert.ok(tools.get(tool).schema.channel, `${tool}'s parameter is \`channel\``);
    assert.ok(!tools.get(tool).schema.name, `…and NOT \`name\``);
  }
});

test("reading a channel that DOES NOT EXIST says so — it must not read as empty", async () => {
  // The distinction this file exists for. An empty answer for a nonexistent channel lets a caller conclude
  // "nobody has said anything" when the truth is "you are looking at nothing", and those license different
  // next actions.
  const res = await call("comms_channel_read", { channel: "never-created" });
  const out = text(res);
  assert.ok(
    res.isError || /not found|does not exist|no such/i.test(out),
    `a missing channel must be distinguishable from an empty one, got: ${out}`,
  );
});

test("a channel WITH a message renders it, behind the safety banner", async () => {
  // THIS TEST IS WHY THE SLICE WAS REVISED. My first version covered a missing channel and an empty one and
  // never a channel with content — so the non-empty branch never executed, and it referenced `SAFETY_HEADER`
  // without the module importing it. Every gate passed and a real read threw `ReferenceError`. The reviewer
  // found it by exercising the branch.
  //
  // The lesson is the one this whole lane keeps relearning from the other side: I test the degenerate cases
  // carefully because that is where bugs hide, and the ORDINARY path went untested. Missing and empty are
  // both early returns. The interesting code is after them.
  await call("comms_channel_create", { name: "chatty", from: "agent-a" });
  const chFile = path.join(MESSAGES_DIR, "channels", "chatty.json");
  const record = JSON.parse(readFileSync(chFile, "utf-8"));
  record.messages = [{ id: "cm1", from: "agent-b", body: "the build is green", timestamp: Date.now() }];
  writeFileSync(chFile, JSON.stringify(record, null, 2));

  const res = await call("comms_channel_read", { channel: "chatty" });
  assert.ok(!res.isError, `reading a non-empty channel failed: ${text(res)}`);
  assert.match(text(res), /the build is green/, "the message body must come back");
  assert.match(text(res), /agent-b/, "…and its sender");
  // Channel messages are written by other agents, so the same banner rule as the inbox applies.
  assert.ok(
    text(res).includes(SAFETY_HEADER),
    "a rendered channel message MUST carry the data-not-instructions banner",
  );
  assert.ok(
    text(res).indexOf(SAFETY_HEADER) < text(res).indexOf("the build is green"),
    "the banner must come BEFORE the content it warns about",
  );
});

test("an existing but empty channel reads as empty, and says which channel", async () => {
  // The other half. These two answers must not be the same string, or the distinction above is cosmetic.
  await call("comms_channel_create", { name: "quiet", from: "agent-a" });
  const empty = text(await call("comms_channel_read", { channel: "quiet" }));
  const missing = text(await call("comms_channel_read", { channel: "never-created" }));
  assert.notEqual(empty, missing, "an empty channel and a missing one must not produce identical answers");
  assert.ok(!/undefined|NaN|\[object Object\]/.test(empty), `leaked a placeholder: ${empty}`);
});

test("a traversal-shaped channel name is refused BY THE NAME GUARD, and writes nothing", async () => {
  // Anti-vacuity in the shape the reviewer accepted for comms_status: a bad name must be refused for being a
  // bad NAME, not merely for being absent. The refusal message is checked, not just the error flag, because
  // "not found" and "invalid" are different guards and only one of them is the security boundary.
  const before = readdirSync(path.join(MESSAGES_DIR, "channels")).sort();
  for (const bad of ["../escape", "a/b", "..", ".hidden"]) {
    // NOTE THE TWO PARAMETER NAMES. `comms_channel_create` takes `name`; join and read take `channel`.
    // Same concept, two spellings — see the dedicated assertion below. Passing the wrong one made every
    // tool here report an undefined channel while still returning isError, which is how my first version of
    // this file "passed" while creating a channel literally called "undefined".
    for (const [tool, args] of [
      ["comms_channel_create", { name: bad, from: "agent-a" }],
      ["comms_channel_join", { channel: bad, from: "agent-a" }],
      ["comms_channel_read", { channel: bad }],
    ]) {
      const res = await call(tool, args);
      assert.equal(res.isError, true, `${tool} must reject ${bad}`);
      assert.match(text(res), /Invalid/i, `${tool}'s refusal of ${bad} must come from the name guard`);
    }
  }
  assert.deepEqual(
    readdirSync(path.join(MESSAGES_DIR, "channels")).sort(), before,
    "a rejected channel name must not have created anything",
  );
});

test("THE GROUP IS INCOMPLETE BY RECORD: comms_channel_send is not here, and must not arrive early", async () => {
  // It is the fifth channel tool and belongs to this subject eventually. It is out because it DELIVERS, which
  // drags spawnTriggeredAgent. A later edit that adds it before that dependency has an owner would silently
  // pull the whole send cluster into this module, and the header explains why — this asserts it.
  assert.ok(!tools.has("comms_channel_send"), "the wrapper must not register the send tool yet");
  const src = readFileSync(path.join(STDIO_DIR, "channel-tools.mjs"), "utf-8");
  assert.doesNotMatch(src, /server\.tool\(\s*\n?\s*"comms_channel_send"/, "send must not be registered here");
  assert.match(src, /JS_SPAWN_TRIGGERED_AGENT_PACKET/, "the header must name the packet that unblocks it");
});

test("the group reaches none of the send cluster", async () => {
  // The packet's central claim, asserted as a test so it cannot rot. Checked against the module's imports and
  // calls rather than against where those functions currently live, so it holds after they move.
  const src = readFileSync(path.join(STDIO_DIR, "channel-tools.mjs"), "utf-8");
  for (const name of ["spawnTriggeredAgent", "deliverMessage", "normalizeSessionMode", "readAgents", "writeAgents"]) {
    assert.doesNotMatch(src, new RegExp(`(?<![\\w.])${name}\\s*\\(`), `${name} must not be CALLED here`);
    assert.doesNotMatch(src, new RegExp(`import\\b[^;]*${name}`), `${name} must not be imported here`);
    // Sanity: the name really does exist somewhere in the bridge, so these are not vacuous negatives.
    assert.equal(isUsedInBridge(name), true, `${name} should still exist in the bridge`);
  }
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state belongs in a tool group");
});

test("each tool is registered exactly once across the bridge", () => {
  for (const name of ["comms_channel_create", "comms_channel_join", "comms_channel_read", "comms_channel_list"]) {
    const owning = toolSources().filter(([, src]) =>
      new RegExp(`server\\.tool\\(\\s*\\n?\\s*"${name}"`).test(src));
    assert.equal(owning.length, 1, `${name} registered by ${owning.map(([f]) => f).join(", ")}`);
    assert.equal(owning[0][0], "channel-tools.mjs");
  }
});

process.on("exit", () => { try { rmSync(STORE, { recursive: true, force: true }); } catch { /* best effort */ } });
