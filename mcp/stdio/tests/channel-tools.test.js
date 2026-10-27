// Channel membership and reading, executed rather than scanned.
//
// `comms_channel_create`, `comms_channel_join`, `comms_channel_read`, `comms_channel_list`, `comms_channel_leave`, `comms_channel_delete`.
// What each tool DOES is held by the reply fixtures (tool-replies.test.js, and the service's Python copy
// against the same cases). Local mode, the file store these tests used to drive, was deleted in 0.9.11.
//
// `comms_channel_send` is deliberately absent from this group; see the module header. These tests assert that
// absence as a property, because the group is incomplete BY RECORD and a later edit should not quietly
// complete it before its dependency has an owner.

import assert from "node:assert/strict";
import test from "node:test";


process.env.AIFY_SERVER_URL = "";
process.env.CLAUDE_MCP_SERVER_URL = "";

const channels = await import("../channel-tools.mjs");
const { SAFETY_HEADER } = await import("../tool-response-format.mjs");
const { z } = await import("zod");

const tools = new Map();
channels.registerChannelTools(
  { tool: (name, description, schema, handler) => tools.set(name, { name, description, schema, handler }) },
  z,
);
const text = (res) => res.content[0].text;
const call = (name, args) => tools.get(name).handler(args);

test("the wrapper registers exactly the six channel tools and exports only itself", () => {
  // `comms_channel_delete` joined them 2026-08-18. It is the most destructive delete an agent can
  // reach — channel, membership and every message ever posted, for every member — so the endpoint
  // gained a creator-or-operator check in the same change. Membership is deliberately not enough:
  // to stop receiving a channel you LEAVE it.
  //
  // AND UNTIL 2026-08-30 YOU COULD NOT. The sentence above, and the delete tool's own description,
  // both routed agents to a leave that no transport exposed — `POST /channels/{name}/leave` existed
  // the whole time. So the only exit an agent could actually take was the one that ends the channel
  // for everybody, while every doc told it to do the opposite. `comms_channel_leave` is that exit.
  assert.deepEqual(
    [...tools.keys()].sort(),
    ["comms_channel_create", "comms_channel_delete", "comms_channel_join", "comms_channel_leave",
     "comms_channel_list", "comms_channel_read"],
  );
  assert.deepEqual(Object.keys(channels).sort(), ["registerChannelTools"]);
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

test("leave takes no agentId, so no agent can remove another from a channel", async () => {
  // `POST /channels/{name}/leave` deletes whatever membership it is handed and never checks the
  // caller owns it. An `agentId` parameter here — which comms_channel_join does have — would turn
  // that into a reachable capability. The absence is the guard.
  const schema = tools.get("comms_channel_leave").schema;
  assert.deepEqual(Object.keys(schema).sort(), ["channel", "from"]);
  // Control: the sibling that DOES accept a third party still does, so this is a real difference
  // between the two tools and not a reader that cannot see parameters at all.
  assert.ok(Object.keys(tools.get("comms_channel_join").schema).includes("agentId"));
});
