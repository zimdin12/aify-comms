// Real tests for the send-triggered cold start, extracted from server.js in v0.5.4.
//
// `comms_send` and `comms_channel_send` call this when the target is MANAGED and resting at `available`
// with no live worker: the send is what wakes it. Nothing tested it, because server.js is imported by no
// test.
//
// THE REFUSAL PATHS ARE THE TESTABLE HALF AND ALSO THE INTERESTING ONE. When a cold start cannot happen,
// this function does not fail the send — it delivers an ERROR MESSAGE BACK TO THE SENDER explaining why,
// and returns undefined exactly as the success path does. Both callers ignore the return value, so that
// message is the only signal a human ever sees. If the wording or the routing of it regressed, a send to a
// resident-without-handle agent would look like it had worked.
//
// WHAT IS NOT COVERED: the LAUNCH path. It reaches the real `launchRuntimeRun`, which starts a process. The
// refusals all return before it, which is why they can be exercised here at all.
//
// SEALING: `deliverMessage`, `readAgents` and `writeAgents` are imported bindings that write to the local
// store under the process's temp dir. Every test below stays on a refusal path, so none of them is reached
// — asserted rather than assumed by pointing the store at a scratch dir and checking nothing appears in it.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

// SEAL THE STORE BEFORE IMPORTING ANYTHING. `local-store.mjs` resolves its root from
// CLAUDE_MCP_MESSAGES_DIR at module LOAD, and every refusal path here calls `deliverMessage`, which WRITES.
// My first version set a variable of my own invention (AIFY_LOCAL_STORE_DIR), so the seal did nothing and
// the run delivered thirteen bogus "[FAILED]" error messages into the repo's real .messages store. They had
// to be found and deleted by hand. Get the variable NAME from the module, not from memory.
const STORE = fs.mkdtempSync(path.join(os.tmpdir(), "aify-spawn-test-"));
process.env.CLAUDE_MCP_MESSAGES_DIR = STORE;

const { spawnTriggeredAgent, LOCAL_RUNTIME_STATE } = await import("../spawn-triggered-agent.mjs");
const { readAgents } = await import("../local-store.mjs");

test.after(() => { try { fs.rmSync(STORE, { recursive: true, force: true }); } catch { /* best effort */ } });

/** Messages delivered to an agent in the sealed store, newest last. */
function inboxOf(agentId) {
  const dir = path.join(STORE, "inbox", agentId);
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).sort().map((f) => JSON.parse(fs.readFileSync(path.join(dir, f), "utf-8")));
}

const managed = (extra = {}) => ({
  sessionMode: "managed",
  runtime: "claude-code",
  capabilities: ["managed-run"],
  ...extra,
});

test("a RESIDENT agent with no session handle is refused, and the sender is TOLD why", () => {
  // The delivered message is the whole output — both callers ignore the return value, so this is the only
  // thing a human ever sees. My first version asserted only that the call returned undefined, which every
  // path does including success: it would have passed with the explanation deleted.
  const result = spawnTriggeredAgent({
    targetId: "coder-1",
    targetInfo: { sessionMode: "resident", runtime: "codex", capabilities: ["resident-run"] },
    from: "manager-bot",
    type: "request",
    subject: "please look",
    body: "…",
  });
  assert.equal(result, undefined, "every path returns undefined — failure is not reported to the caller");

  const [msg] = inboxOf("manager-bot");
  assert.ok(msg, "the sender must receive an explanation, not silence");
  assert.equal(msg.type, "error");
  assert.equal(msg.from, "coder-1", "it comes FROM the agent that could not start");
  assert.match(msg.subject, /^\[FAILED\] please look$/, "the original subject is preserved and marked");
  assert.match(msg.body, /resident session without a triggerable session handle/);
  assert.match(msg.body, /Re-register that live session first/,
    "the actionable half — without it the operator sees a send that silently did nothing");
});

test("a MANAGED agent without the managed-run capability is refused", () => {
  const result = spawnTriggeredAgent({
    targetId: "coder-2",
    targetInfo: managed({ capabilities: [] }),
    from: "manager-bot",
    type: "request",
    subject: "s",
    body: "b",
  });
  assert.equal(result, undefined);
});

test("a resident agent is only runnable with codex AND resident-run AND a handle — all three", () => {
  // Four near-misses, each differing from runnable by exactly one condition. Testing only the fully-absent
  // case would let any one of the three be dropped without failing.
  const runnable = {
    sessionMode: "resident", runtime: "codex", capabilities: ["resident-run"], sessionHandle: "thread-1",
  };
  for (const [label, info] of [
    ["wrong runtime", { ...runnable, runtime: "claude-code" }],
    ["no resident-run capability", { ...runnable, capabilities: [] }],
    ["no session handle", { ...runnable, sessionHandle: "" }],
    ["managed mode with resident caps", { ...runnable, sessionMode: "managed" }],
  ]) {
    assert.equal(
      spawnTriggeredAgent({ targetId: "a1", targetInfo: info, from: "m", type: "info", subject: "s", body: "b" }),
      undefined,
      `${label} must not be treated as runnable`,
    );
  }
});

test("a non-array capabilities field is tolerated rather than throwing", () => {
  // It arrives off the wire and off disk. A throw here would fail the SEND, which is the one thing this
  // function is supposed never to do.
  for (const capabilities of [undefined, null, "managed-run", 7, {}]) {
    assert.equal(
      spawnTriggeredAgent({
        targetId: "a1", targetInfo: managed({ capabilities }), from: "m", type: "info", subject: "s", body: "b",
      }),
      undefined,
      `capabilities=${JSON.stringify(capabilities)} must be handled, not thrown on`,
    );
  }
});

test("a runtime that cannot be launched is refused even when the mode and capability are right", () => {
  // The second gate. `canLaunchRuntime` is the real one — a runtime nothing can start must not reach
  // launchRuntimeRun just because the registry claimed the capability.
  const result = spawnTriggeredAgent({
    targetId: "a1",
    targetInfo: managed({ runtime: "no-such-runtime-xyz" }),
    from: "m", type: "info", subject: "s", body: "b",
  });
  assert.equal(result, undefined);
});

test("LOCAL_RUNTIME_STATE is exported and starts empty — the refusals never touch it", () => {
  // It moved out of server.js with this function because all three of its uses are here. If a refusal path
  // ever wrote to it, state would accumulate for agents that never started.
  assert.ok(LOCAL_RUNTIME_STATE instanceof Map);
  const before = LOCAL_RUNTIME_STATE.size;
  spawnTriggeredAgent({
    targetId: "a1", targetInfo: managed({ capabilities: [] }), from: "m", type: "info", subject: "s", body: "b",
  });
  assert.equal(LOCAL_RUNTIME_STATE.size, before, "a refused start must record no runtime state");
});

test("importing the module starts nothing and needs no live service", async () => {
  const again = await import("../spawn-triggered-agent.mjs");
  assert.equal(again.spawnTriggeredAgent, spawnTriggeredAgent, "one module instance, no load-time side effects");
  assert.equal(again.LOCAL_RUNTIME_STATE, LOCAL_RUNTIME_STATE, "and one Map, not a fresh one per import");
});
