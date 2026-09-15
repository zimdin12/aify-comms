// agent-lease-attach.mjs: a detached process joins its launcher's agent lease, without holding its caller.

import assert from "node:assert/strict";
import test from "node:test";

import { attachToAgentLease, leaseAttachArgv } from "../agent-lease-attach.mjs";

const ENV = { AIFY_AGENT_ID: "mc-senior-dev", AIFY_AGENT_LEASE: "36200" };

test("the attach names the agent, the launcher's instance, the pid and its kind", () => {
  assert.deepEqual(leaseAttachArgv({ env: ENV, pid: 113008, kind: "gateway", helper: "/w/bin/aify-agent-lease.mjs" }),
    ["/w/bin/aify-agent-lease.mjs", "attach", "--agent", "mc-senior-dev", "--instance", "36200", "--pid", "113008", "--kind", "gateway"]);
});

test("no lease, no agent or no pid means no attach", () => {
  for (const env of [{}, { AIFY_AGENT_ID: "a" }, { AIFY_AGENT_LEASE: "5" }, { AIFY_AGENT_ID: "a", AIFY_AGENT_LEASE: "x" }]) {
    assert.equal(leaseAttachArgv({ env, pid: 7, kind: "k", helper: "h" }), null, JSON.stringify(env));
  }
  assert.equal(leaseAttachArgv({ env: ENV, pid: 0, kind: "k", helper: "h" }), null);
});

test("it runs detached and unreferenced, so ensure-host is not held; a failure to spawn is swallowed", () => {
  const calls = [];
  const child = { on() {}, unref() { calls.push("unref"); } };
  assert.equal(attachToAgentLease({ pid: 9, kind: "gateway", env: ENV, helper: "h", spawn: (cmd, argv, opts) => { calls.push([cmd, argv[1], opts.detached, opts.stdio]); return child; } }), true);
  assert.deepEqual(calls, [[process.execPath, "attach", true, "ignore"], "unref"]);
  assert.equal(attachToAgentLease({ pid: 9, kind: "gateway", env: ENV, helper: "h", spawn: () => { throw new Error("EACCES"); } }), false);
  assert.equal(attachToAgentLease({ pid: 9, kind: "gateway", env: {}, helper: "h", spawn: () => { throw new Error("must not spawn"); } }), false);
});

test("the default helper resolves to the installed aify-wrapper CLI", async () => {
  const fs = await import("node:fs");
  const { fileURLToPath } = await import("node:url");
  assert.ok(fs.existsSync(fileURLToPath(import.meta.resolve("aify-wrapper/bin/aify-agent-lease.mjs"))));
});
