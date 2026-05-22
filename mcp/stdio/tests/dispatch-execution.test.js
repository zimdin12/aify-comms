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

// Updated 2026-05-22: hermes is now in NATIVE_MANAGED_RUNTIMES
// (mirrors service-side _NATIVE_MANAGED_RUNTIMES). Managed hermes
// dispatches are claimed via the native RPC path through
// createHermesController. The capabilities-based gate is OR'd with
// runtime membership, so managed Hermes is claimable without an
// explicit native-managed-run capability now.
assert.deepEqual(
  supportedExecutionModes({ sessionMode: "managed", runtime: "hermes", capabilities: ["managed-run"] }),
  ["managed"],
  "managed Hermes must be claimable via native RPC (now in NATIVE_MANAGED_RUNTIMES)",
);

assert.deepEqual(
  supportedExecutionModes({ sessionMode: "resident", runtime: "pi", capabilities: ["resident-run"] }),
  ["resident"],
  "resident Pi should remain claimable",
);

console.log("dispatch-execution.test.js: all assertions passed");
