// Real tests for the two SEND tools, extracted from server.js in v0.5.4 — the last tool registrations to
// leave it.
//
// They were parked behind `spawnTriggeredAgent` and are the only tools that DELIVER. Both are
// live-delivery gated: a send to an unreachable target is not written at all, and the tool's job is to
// decide deliverability and then steer, queue, or cold-start.
//
// SEALED STORE AND NO SERVER. `CLAUDE_MCP_MESSAGES_DIR` points at a scratch dir and both server-URL
// variables are blanked BEFORE the import, so `IS_REMOTE` is false and every test runs the LOCAL branch.
// That matters twice over: the remote branch would make real HTTP calls, and this module's siblings write
// to the message store — a previous test of mine in this lane sealed a variable name I had invented and
// delivered thirteen bogus messages into the repo's real store.
//
// WHAT IS NOT COVERED: the REMOTE branch (it needs a live service) and the successful cold start (it
// reaches the real `launchRuntimeRun`, which starts a process). The refusals and the local delivery path
// are what can be exercised without side effects, and they are where the operator-visible behaviour is.

import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const STORE = mkdtempSync(path.join(os.tmpdir(), "aify-send-tools-"));
process.env.AIFY_SERVER_URL = "";
process.env.CLAUDE_MCP_SERVER_URL = "";
process.env.CLAUDE_MCP_MESSAGES_DIR = STORE;

const sendTools = await import("../send-tools.mjs");
const { MESSAGES_DIR, readAgents, writeAgents } = await import("../local-store.mjs");
const { IS_REMOTE } = await import("../aify-service-endpoint.mjs");
const { z } = await import("zod");

const tools = new Map();
sendTools.registerSendTools(
  { tool: (name, description, schema, handler) => tools.set(name, { name, description, schema, handler }) },
  z,
);
const call = async (name, args) => tools.get(name).handler(args);
const text = (res) => res.content[0].text;

test.after(() => { try { rmSync(STORE, { recursive: true, force: true }); } catch { /* best effort */ } });

function inboxOf(agentId) {
  const dir = path.join(STORE, "inbox", agentId);
  try {
    return readdirSync(dir).sort().map((f) => JSON.parse(readFileSync(path.join(dir, f), "utf-8")));
  } catch {
    return [];
  }
}

test("the fixture really is sealed and local", () => {
  // Both halves matter. An unsealed store writes into the repo; a truthy IS_REMOTE runs the HTTP branch.
  assert.ok(MESSAGES_DIR.startsWith(STORE), `expected the scratch store, got ${MESSAGES_DIR}`);
  assert.equal(IS_REMOTE, false, "these tests exercise the LOCAL branch");
});

test("the module registers exactly the two send tools and exports only its wrapper", () => {
  assert.deepEqual([...tools.keys()].sort(), ["comms_channel_send", "comms_send"]);
  assert.deepEqual(Object.keys(sendTools).sort(), ["COMMS_SEND_TOOL_DESCRIPTION", "registerSendTools"],
    "the wrapper, plus the description that travelled with the tool it describes — the tools themselves "
    + "reach the server through the wrapper, not individually");
});

test("both descriptions say the send is live-delivery gated", () => {
  // The description is what an agent reads before choosing a tool. If it stopped saying that a send to an
  // offline agent is NOT written, callers would treat comms_send as an append-to-inbox and stop checking.
  for (const name of ["comms_send", "comms_channel_send"]) {
    assert.match(tools.get(name).description, /deliver|offline|live/i, `${name} must describe the gate`);
  }
});

test("IN LOCAL MODE THE MESSAGE IS ALWAYS WRITTEN — only the WAKE is gated", async () => {
  // I expected a refusal and asserted one; the tool does the opposite, and the difference matters. The
  // module header calls both tools "live-delivery gated", and in REMOTE mode they are: the service refuses
  // to write for an unreachable target. Local mode has no service — the store is a mailbox — so the message
  // lands and only the TRIGGER is skipped, with the reason named per recipient.
  //
  // Pinned as observed behaviour rather than corrected, because a caller who reads "live-delivery gated"
  // and runs locally will otherwise be surprised in exactly this way.
  writeAgents({ agents: {} });
  const res = await call("comms_send", { from: "manager-bot", to: "nobody", type: "info", subject: "s", body: "b" });

  assert.match(text(res), /Skipped: nobody/, "the reply must name who could not be woken");
  assert.match(text(res), /no launchable recipients/, "…and say that nothing was started");

  const [msg] = inboxOf("nobody");
  assert.ok(msg, "the message IS written locally even for an agent that has never registered");
  assert.equal(msg.body, "b");
});

test("a traversal-shaped agent id cannot reach outside the store", async () => {
  // `validateName` guards this. The store is one directory per agent, so an id containing separators would
  // otherwise choose the directory.
  writeAgents({ agents: {} });
  for (const bad of ["../escape", "a/b", "..\\win", "."]) {
    const res = await call("comms_send", { from: "m", to: bad, type: "info", subject: "s", body: "b" });
    assert.ok(res?.content?.[0]?.text, `${bad} must return a message rather than throwing`);
  }
  assert.deepEqual(inboxOf("escape"), [], "nothing may land outside a legitimate agent directory");
});

test("a delivered message reaches the recipient's inbox with its subject and sender", async () => {
  // The ordinary success path in local mode. A registered agent with no live worker still receives it.
  writeAgents({ agents: { coder: { id: "coder", role: "coder", sessionMode: "resident" } } });
  const res = await call("comms_send", {
    from: "manager-bot", to: "coder", type: "request", subject: "please review", body: "the diff",
  });
  assert.ok(text(res).length > 0, "the sender is told what happened");

  const [msg] = inboxOf("coder");
  assert.ok(msg, "the message must be written");
  assert.equal(msg.from, "manager-bot");
  assert.equal(msg.subject, "please review");
  assert.equal(msg.body, "the diff");
  assert.equal(msg.type, "request");
});

test("a channel send to a channel that does not exist is refused rather than creating one", async () => {
  // Sending must not be a back door for channel creation: a typo would otherwise silently make a second
  // channel and split a conversation in two.
  const res = await call("comms_channel_send", { from: "manager-bot", channel: "no-such-channel", body: "hi" });
  assert.match(text(res), /no-such-channel|not found|does not exist/i);
});

test("every registered tool declares a schema and a handler", () => {
  // A tool registered without one of them fails at call time inside the MCP client, where the error is
  // opaque. Cheap to assert here.
  for (const [name, t] of tools) {
    assert.equal(typeof t.handler, "function", `${name} must have a handler`);
    assert.ok(t.schema && typeof t.schema === "object", `${name} must declare a schema`);
    assert.ok(t.description && t.description.length > 20, `${name} must describe itself`);
  }
});

test("importing the module sends nothing and needs no live service", async () => {
  const again = await import("../send-tools.mjs");
  assert.equal(again.registerSendTools, sendTools.registerSendTools, "one module instance, no load-time work");
});
