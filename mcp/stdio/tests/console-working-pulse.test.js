#!/usr/bin/env node
// Pure-predicate test of the pulse gate: claude+working -> console-working lease;
// claude+idle/unknown -> nothing; non-claude -> legacy terminal pulse; no agent -> none.
import assert from "node:assert/strict";
import { decideConsolePulse } from "../server.js";

assert.deepEqual(
  decideConsolePulse({ runtime: "claude-code", consoleClass: "working", agentId: "a1" }),
  { kind: "console-working", agentId: "a1" },
);
assert.deepEqual(
  decideConsolePulse({ runtime: "claude-code", consoleClass: "idle", agentId: "a1" }),
  { kind: "none" },
);
assert.deepEqual(
  decideConsolePulse({ runtime: "claude-code", consoleClass: "unknown", agentId: "a1" }),
  { kind: "none" },
);
// Defense-in-depth (#224): a transient "unknown" footer frame refreshes the lease ONLY when a
// turn is already known in flight — never at rest, never on a clear "idle" reading.
assert.deepEqual(
  decideConsolePulse({ runtime: "claude-code", consoleClass: "unknown", agentId: "a1", turnInFlight: true }),
  { kind: "console-working", agentId: "a1" },
);
assert.deepEqual(
  decideConsolePulse({ runtime: "claude-code", consoleClass: "idle", agentId: "a1", turnInFlight: true }),
  { kind: "none" },
);
assert.deepEqual(
  decideConsolePulse({ runtime: "claude-code", consoleClass: null, agentId: "a1", turnInFlight: true }),
  { kind: "none" },
);
assert.deepEqual(
  decideConsolePulse({ runtime: "claude-code", consoleClass: null, agentId: "a1" }),
  { kind: "none" },
);
// Non-claude runtimes do not get an output-based pulse (native detectors own it; the
// legacy terminal pulse was dead and is kept disabled).
assert.deepEqual(
  decideConsolePulse({ runtime: "codex", consoleClass: null, agentId: "a2" }),
  { kind: "none" },
);
assert.deepEqual(
  decideConsolePulse({ runtime: "hermes", consoleClass: null, agentId: "a3" }),
  { kind: "none" },
);
assert.deepEqual(
  decideConsolePulse({ runtime: "claude-code", consoleClass: "working", agentId: "" }),
  { kind: "none" },
);

console.log("console-working-pulse.test.js: all assertions passed");
