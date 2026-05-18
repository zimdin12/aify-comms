#!/usr/bin/env node
import assert from "node:assert/strict";
import { advertisedEnvironmentRuntimes, advertisedTerminalRuntimes } from "../environment-runtimes.js";

const availabilityFor = (runtime) => ({
  available: runtime !== "hermes",
  message: runtime === "hermes" ? "Hermes launcher unavailable" : `${runtime} available`,
});

const runtimes = advertisedEnvironmentRuntimes({ availabilityFor });
assert.deepEqual(
  runtimes.map((entry) => entry.runtime),
  ["codex", "claude-code", "hermes", "opencode", "pi"],
  "environment runtime advertisement should include unavailable runtimes with reasons",
);
assert.equal(runtimes.find((entry) => entry.runtime === "hermes")?.available, false);
assert.equal(runtimes.find((entry) => entry.runtime === "hermes")?.unavailableReason, "Hermes launcher unavailable");

assert.deepEqual(
  advertisedTerminalRuntimes({ availabilityFor }),
  ["codex", "claude-code", "opencode", "pi"],
  "terminal runtimes must remain available-only",
);
assert.deepEqual(
  advertisedTerminalRuntimes({ availabilityFor, terminalSupported: false }),
  [],
  "terminal runtimes must be empty when the bridge cannot load PTY support",
);

console.log("environment-runtimes.test.js: all assertions passed");
