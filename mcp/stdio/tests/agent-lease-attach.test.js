// agent-lease-attach.mjs: a detached process joins its launcher's agent lease, without holding its caller.

import assert from "node:assert/strict";
import test from "node:test";

import { attachToAgentLease, leaseAttachArgv } from "../agent-lease-attach.mjs";

const ENV = { AIFY_AGENT_ID: "mc-senior-dev", AIFY_AGENT_LEASE: "36200" };

test("the attach names the agent, the launcher's instance, the pid and its kind", () => {
  assert.deepEqual(leaseAttachArgv({ env: ENV, agentId: "mc-senior-dev", pid: 113008, kind: "gateway", helper: "/w/bin/aify-agent-lease.mjs" }),
    ["/w/bin/aify-agent-lease.mjs", "attach", "--agent", "mc-senior-dev", "--instance", "36200", "--pid", "113008", "--kind", "gateway"]);
});

test("no lease, no agent or no pid means no attach", () => {
  for (const env of [{}, { AIFY_AGENT_ID: "a" }, { AIFY_AGENT_ID: "a", AIFY_AGENT_LEASE: "x" }]) {
    assert.equal(leaseAttachArgv({ env, agentId: "a", pid: 7, kind: "k", helper: "h" }), null, JSON.stringify(env));
  }
  assert.equal(leaseAttachArgv({ env: ENV, agentId: "mc-senior-dev", pid: 0, kind: "k", helper: "h" }), null);
  assert.equal(leaseAttachArgv({ env: ENV, pid: 7, kind: "k", helper: "h" }), null, "no caller agent");
});

test("the agent is the caller's: a lease inherited from ANOTHER agent's launcher is never joined", () => {
  // ensure-host for agent-b run from agent-a's shell inherits agent-a's lease. Attached there, agent-b's
  // gateway would be killed by agent-a's next replace.
  assert.equal(leaseAttachArgv({ env: ENV, agentId: "agent-b", pid: 7, kind: "gateway", helper: "h" }), null);
  // CONTROL: the same call for the agent the lease belongs to attaches, and so does one with no inherited identity.
  assert.ok(leaseAttachArgv({ env: ENV, agentId: "mc-senior-dev", pid: 7, kind: "gateway", helper: "h" }));
  assert.ok(leaseAttachArgv({ env: { AIFY_AGENT_LEASE: "5" }, agentId: "agent-b", pid: 7, kind: "gateway", helper: "h" }));
});

test("it runs detached and unreferenced, so ensure-host is not held; a failure to spawn is swallowed", () => {
  const calls = [];
  const child = { on() {}, unref() { calls.push("unref"); } };
  assert.equal(attachToAgentLease({ agentId: "mc-senior-dev", pid: 9, kind: "gateway", env: ENV, helper: "h", spawn: (cmd, argv, opts) => { calls.push([cmd, argv[1], opts.detached, opts.stdio]); return child; } }), true);
  assert.deepEqual(calls, [[process.execPath, "attach", true, "ignore"], "unref"]);
  assert.equal(attachToAgentLease({ agentId: "mc-senior-dev", pid: 9, kind: "gateway", env: ENV, helper: "h", spawn: () => { throw new Error("EACCES"); } }), false);
  assert.equal(attachToAgentLease({ pid: 9, kind: "gateway", env: {}, helper: "h", spawn: () => { throw new Error("must not spawn"); } }), false);
});

test("the default helper resolves to the installed aify-wrapper CLI", async () => {
  const fs = await import("node:fs");
  const { fileURLToPath } = await import("node:url");
  assert.ok(fs.existsSync(fileURLToPath(import.meta.resolve("aify-wrapper/bin/aify-agent-lease.mjs"))));
});
