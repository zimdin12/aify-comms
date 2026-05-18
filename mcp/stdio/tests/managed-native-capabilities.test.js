#!/usr/bin/env node
import assert from "node:assert/strict";
import { defaultCapabilitiesForRuntime } from "../runtimes.js";

assert.deepEqual(
  defaultCapabilitiesForRuntime("codex", "managed"),
  ["managed-run", "native-managed-run", "resume", "interrupt", "steer", "spawn"],
  "managed Codex should advertise native managed dispatch",
);

assert.deepEqual(
  defaultCapabilitiesForRuntime("opencode", "managed"),
  ["managed-run", "native-managed-run", "resume", "interrupt", "spawn"],
  "managed OpenCode should advertise native managed dispatch",
);

assert.deepEqual(
  defaultCapabilitiesForRuntime("claude-code", "managed"),
  ["resume", "interrupt", "spawn"],
  "managed Claude should not advertise active managed dispatch until a native adapter exists",
);

console.log("managed-native-capabilities.test.js: all assertions passed");
