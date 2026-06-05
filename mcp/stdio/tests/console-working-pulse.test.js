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
assert.deepEqual(
  decideConsolePulse({ runtime: "claude-code", consoleClass: null, agentId: "a1" }),
  { kind: "none" },
);
assert.deepEqual(
  decideConsolePulse({ runtime: "codex", consoleClass: null, agentId: "a2" }),
  { kind: "terminal-pulse", agentId: "a2" },
);
assert.deepEqual(
  decideConsolePulse({ runtime: "claude-code", consoleClass: "working", agentId: "" }),
  { kind: "none" },
);

console.log("console-working-pulse.test.js: all assertions passed");
