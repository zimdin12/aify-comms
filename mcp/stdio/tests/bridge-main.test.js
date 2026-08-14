// Bringing the bridge up, tested by CALLING it.
//
// Extracted from server.js in v0.5.4, where nothing could reach it. `main()` is what every bridge start
// runs: connect the MCP transport, arm the harness-death guard, kick auto-registration.
//
// THE HARNESS GUARD IS WHY THIS IS WORTH TESTING. An MCP-child bridge is loaded by a claude/codex/hermes
// harness; when that harness dies the child must go too, or it lingers as an orphan the service has to
// reap. Six of these were once found reparented to the WSL init relay after ~10 hours. The guard polls
// the pid captured at STARTUP, because reparenting hides the death from `process.ppid`.

import assert from "node:assert/strict";
import test from "node:test";

import { main } from "../bridge-main.mjs";

/** A server double whose connect() records the transport it was handed. */
function fakeServer() {
  const connected = [];
  return { connected, connect: async (t) => { connected.push(t); } };
}

class FakeTransport {}

function deps(over = {}) {
  return {
    ORIGINAL_PARENT_PID: 0,
    StdioServerTransport: FakeTransport,
    ensureDispatchLoop: () => {},
    server: fakeServer(),
    shutdownWithStatus: () => {},
    ...over,
  };
}

test("it connects the MCP transport it is handed", async () => {
  // The transport is injected rather than imported so an RPC-child bridge never loads the SDK. If that
  // ever became a static import, this test would still pass — but `rpc-child-bridge-disabled` would not.
  const d = deps();
  await main(d);
  assert.equal(d.server.connected.length, 1, "the server must be connected exactly once");
  assert.ok(d.server.connected[0] instanceof FakeTransport, "…with the transport passed in");
});

test("auto-registration is kicked off, and its failure does NOT reject main", async () => {
  // `.catch` on the auto-register promise. Registration talks to the service; if a transient failure
  // escaped here, the bridge would die at startup instead of retrying — and an MCP harness would report
  // it as the server failing to launch.
  const d = deps();
  await assert.doesNotReject(() => main(d));
});

test("NO HARNESS GUARD IS ARMED when there is no controlling parent", async () => {
  // `ORIGINAL_PARENT_PID > 1`. A pid of 0 or 1 means no harness to watch — arming a poll against init
  // would either never fire or, worse, treat init's existence as the harness being alive forever.
  let stopped = 0;
  await main(deps({ ORIGINAL_PARENT_PID: 0, shutdownWithStatus: () => { stopped += 1; } }));
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(stopped, 0);

  await main(deps({ ORIGINAL_PARENT_PID: 1, shutdownWithStatus: () => { stopped += 1; } }));
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(stopped, 0, "pid 1 is init, not a harness");
});

test("the guard tolerates a LIVE parent without shutting anything down", async () => {
  // Armed against this very process, which is certainly alive. The guard must sit quiet — a false
  // positive here kills a healthy bridge.
  let stopped = 0;
  await main(deps({
    ORIGINAL_PARENT_PID: process.pid,
    shutdownWithStatus: () => { stopped += 1; },
  }));
  await new Promise((r) => setTimeout(r, 120));
  assert.equal(stopped, 0, "a live parent must never trigger shutdown");
});

test("the guard's timer is UNREF'D so it cannot hold the process open", async () => {
  // `harnessGuard.unref()`. Without it a bridge whose work is done would be kept alive by its own
  // watchdog — and this test file would hang for three seconds per case rather than exiting.
  //
  // Observed rather than asserted structurally: if the interval were not unref'd, node would keep this
  // process alive past the test and the runner would report a timeout instead of a pass.
  await main(deps({ ORIGINAL_PARENT_PID: process.pid }));
  assert.ok(true, "reaching here without the runner hanging is the assertion");
});
