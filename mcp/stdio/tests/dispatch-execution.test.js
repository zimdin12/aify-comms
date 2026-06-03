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
  [],
  "resident Pi must not be claimable; OMP is single-client and uses managed RPC",
);

assert.deepEqual(
  supportedExecutionModes({ sessionMode: "resident", runtime: "opencode", capabilities: ["resident-run"] }),
  [],
  "resident OpenCode must not be claimable until a real multi-client resident surface exists",
);

// 2026-06-03 fabricated-reply fix: resident CODEX is claimed by its main bridge
// (its in-process bridge is the delivery surface), but resident HERMES must NOT
// be — its channel-sidecar delivery loop (hermes-managed-host.js) is the sole
// claimer. A resident hermes main bridge claiming the run would route it through
// ChannelDelegatedController and auto-mirror the "channel/resident dispatch
// delegated…" summary as a fabricated reply (no real turn, nothing in the TUI).
assert.deepEqual(
  supportedExecutionModes({ sessionMode: "resident", runtime: "codex", capabilities: ["resident-run"] }),
  ["resident"],
  "resident Codex IS claimable by its main bridge (in-process delivery surface)",
);

assert.deepEqual(
  supportedExecutionModes({ sessionMode: "resident", runtime: "hermes", capabilities: ["resident-run"] }),
  [],
  "resident Hermes must NOT be claimed by the main bridge — its channel-sidecar loop owns delivery (prevents the ChannelDelegatedController fabricated reply)",
);

console.log("dispatch-execution.test.js: all assertions passed");
