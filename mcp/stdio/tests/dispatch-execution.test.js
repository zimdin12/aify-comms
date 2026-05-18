#!/usr/bin/env node
import assert from "node:assert/strict";
import { supportedExecutionModes } from "../dispatch-execution.js";

assert.deepEqual(
  supportedExecutionModes({ sessionMode: "managed", runtime: "claude-code", capabilities: ["managed-run"] }),
  [],
  "managed Claude should not be claimed by the bridge for active dispatch",
);

assert.deepEqual(
  supportedExecutionModes({ sessionMode: "managed", runtime: "codex", capabilities: ["managed-run"] }),
  ["managed"],
  "managed Codex with stale pre-native capability should still be claimed by runtime adapter support",
);

assert.deepEqual(
  supportedExecutionModes({ sessionMode: "managed", runtime: "pi", capabilities: [] }),
  ["managed"],
  "managed Pi should claim by runtime adapter support even if persisted capabilities are stale/missing",
);
assert.deepEqual(
  supportedExecutionModes({ sessionMode: "managed", runtime: "codex", capabilities: ["native-managed-run", "managed-run"] }),
  ["managed"],
  "managed Codex should remain claimable through native managed dispatch",
);

assert.deepEqual(
  supportedExecutionModes({ sessionMode: "managed", runtime: "hermes", capabilities: ["managed-run"] }),
  [],
  "managed Hermes should not be claimed without native-managed-run",
);

assert.deepEqual(
  supportedExecutionModes({ sessionMode: "resident", runtime: "pi", capabilities: ["resident-run"] }),
  ["resident"],
  "resident Pi should remain claimable",
);

console.log("dispatch-execution.test.js: all assertions passed");
