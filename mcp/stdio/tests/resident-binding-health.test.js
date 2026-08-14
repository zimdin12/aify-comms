// Real tests for the resident-binding health decision, extracted from server.js in v0.5.4.
//
// THE HYSTERESIS IS THE POINT. Answering "yes" costs a resident agent its binding, so one unreachable
// probe must not be enough — a restart, a busy moment, or a 1.2s timeout under load would otherwise tear
// down a healthy agent. Two CONSECUTIVE failures are required, and the counter is cleared on every success
// and every not-applicable answer so they cannot accumulate over an afternoon.
//
// server.js is imported by no test, so none of this had coverage.
//
// A REAL CLOSED PORT rather than a stubbed probe: `codexAppServerReachable` is an imported binding that
// cannot be monkey-patched, and pointing it at a port nothing is listening on is what "unreachable"
// actually means. The reachable case needs a real Codex app-server handshake, which a plain HTTP server
// cannot fake, so it is NOT asserted here — said plainly rather than approximated.

import assert from "node:assert/strict";
import net from "node:net";
import test from "node:test";

import {
  RESIDENT_BINDING_FAILURES,
  RESIDENT_BINDING_LOST_AFTER_FAILURES,
  residentRuntimeBindingLost,
} from "../resident-binding-health.mjs";

// A port that was bound and then closed: connections are refused immediately, so the probe fails fast
// rather than waiting out its timeout on every assertion.
const DEAD_PORT = await new Promise((resolve) => {
  const s = net.createServer();
  s.listen(0, "127.0.0.2", () => {
    const { port } = s.address();
    s.close(() => resolve(port));
  });
});

const resident = (extra = {}) => ({
  sessionMode: "resident",
  runtime: "codex",
  sessionHandle: "thread-1",
  runtimeConfig: { appServerUrl: `http://127.0.0.2:${DEAD_PORT}` },
  ...extra,
});

test("only a RESIDENT CODEX agent is probed at all", async () => {
  // A managed agent's liveness is decided elsewhere, and no other runtime has an app-server to probe.
  RESIDENT_BINDING_FAILURES.clear();
  for (const info of [
    resident({ sessionMode: "managed" }),
    resident({ runtime: "claude-code" }),
    resident({ runtime: "hermes" }),
    resident({ sessionMode: "managed", runtime: "pi" }),
  ]) {
    assert.equal(await residentRuntimeBindingLost("a1", info), false);
  }
  assert.equal(RESIDENT_BINDING_FAILURES.size, 0, "a non-applicable agent must not be counted");
});

test("no app-server URL or no session handle CLEARS the count and answers false", async () => {
  // Not-yet-bound is not the same as lost. Leaving a stale count here would make the next real failure
  // the second one, and tear the agent down a probe early.
  RESIDENT_BINDING_FAILURES.set("a1", 1);
  assert.equal(await residentRuntimeBindingLost("a1", resident({ runtimeConfig: {} })), false);
  assert.equal(RESIDENT_BINDING_FAILURES.has("a1"), false);

  RESIDENT_BINDING_FAILURES.set("a1", 1);
  assert.equal(await residentRuntimeBindingLost("a1", resident({ sessionHandle: "" })), false);
  assert.equal(RESIDENT_BINDING_FAILURES.has("a1"), false);
});

test("ONE unreachable probe is not enough — two consecutive are", async () => {
  RESIDENT_BINDING_FAILURES.clear();
  const first = await residentRuntimeBindingLost("a1", resident());
  assert.equal(first, false, "a single transient failure must not cost the agent its binding");
  assert.equal(RESIDENT_BINDING_FAILURES.get("a1"), 1);

  const second = await residentRuntimeBindingLost("a1", resident());
  assert.equal(second, true, "the second consecutive failure is the decision");
  assert.equal(RESIDENT_BINDING_FAILURES.get("a1"), RESIDENT_BINDING_LOST_AFTER_FAILURES);
});

test("the count is PER AGENT — one failing agent does not condemn another", async () => {
  RESIDENT_BINDING_FAILURES.clear();
  await residentRuntimeBindingLost("a1", resident());
  const otherFirst = await residentRuntimeBindingLost("a2", resident());
  assert.equal(otherFirst, false, "a2's first failure must still be its first");
  assert.equal(RESIDENT_BINDING_FAILURES.get("a1"), 1);
  assert.equal(RESIDENT_BINDING_FAILURES.get("a2"), 1);
});

test("a not-applicable answer resets a partial count, so failures must be CONSECUTIVE", async () => {
  // The agent fails once, then switches to managed (or loses its handle) and back. Without the reset the
  // next failure would be counted as the second and end the binding.
  RESIDENT_BINDING_FAILURES.clear();
  await residentRuntimeBindingLost("a1", resident());
  assert.equal(RESIDENT_BINDING_FAILURES.get("a1"), 1);

  await residentRuntimeBindingLost("a1", resident({ sessionHandle: "" }));
  assert.equal(RESIDENT_BINDING_FAILURES.has("a1"), false, "the partial count must be cleared");

  assert.equal(await residentRuntimeBindingLost("a1", resident()), false,
    "…so the next failure is a first failure again");
});

test("the threshold is a named constant, not a literal buried in the check", () => {
  assert.equal(RESIDENT_BINDING_LOST_AFTER_FAILURES, 2);
});
