#!/usr/bin/env node
import assert from "node:assert/strict";
import { defaultCapabilitiesForRuntime } from "../runtimes.js";

// Plan 2 (2026-05-25): defaultCapabilitiesForRuntime now derives from the
// runtime adapter's supports_* flags and no longer emits native-managed-run
// as a separate capability — every managed-capable adapter is now treated as
// native by the bridge. Capabilities mirror Python's _default_capabilities_for
// in service/routers/api_v2.py.

assert.deepEqual(
  defaultCapabilitiesForRuntime("codex", "managed"),
  ["managed-run", "resume", "interrupt", "steer", "spawn"],
  "managed Codex should advertise managed dispatch with steer + interrupt",
);

assert.deepEqual(
  defaultCapabilitiesForRuntime("opencode", "managed"),
  ["managed-run", "resume", "interrupt", "steer", "spawn"],
  "managed OpenCode should advertise promptAsync steer",
);

assert.deepEqual(
  defaultCapabilitiesForRuntime("claude-code", "managed"),
  ["managed-run", "resume", "interrupt", "steer", "spawn"],
  "managed Claude now mirrors the ClaudeAdapter supports_* flags",
);

console.log("managed-native-capabilities.test.js: all assertions passed");
