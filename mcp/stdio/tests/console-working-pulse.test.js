#!/usr/bin/env node
// Pure-predicate test of the pulse gate: claude+working -> console-working lease;
// claude+idle/unknown -> nothing; non-claude -> legacy terminal pulse; no agent -> none.
import assert from "node:assert/strict";
// Imported from its OWNER. It used to come from `server.js`, the bin entry point, so a test of a
// pure decision function loaded the entire bridge.
import { decideConsolePulse } from "../console-pulse.mjs";

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

// ── ownership, added when this moved out of the bin entry point in v0.5.4 ──────────────────────────
import fsOwn from "node:fs";
import pathOwn from "node:path";
import { fileURLToPath as f2u } from "node:url";
import { declaringModules, isUsedInBridge } from "./bridge-sources.mjs";

{
  const stdio = pathOwn.resolve(pathOwn.dirname(f2u(import.meta.url)), "..");
  assert.deepEqual(declaringModules("decideConsolePulse"),
    [{ file: "console-pulse.mjs", kind: "function" }],
    "two copies would let the console and the poll loop disagree about what counts as generating");
  assert.ok(isUsedInBridge("decideConsolePulse"), "an unused decision function is dead code");
  const src = fsOwn.readFileSync(pathOwn.join(stdio, "console-pulse.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m, "no module-level mutable state");
  assert.deepEqual([...src.matchAll(/^import .* from "([^"]+)";$/gm)].map((m) => m[1]), [],
    "it decides from its arguments alone and must import nothing");
}
