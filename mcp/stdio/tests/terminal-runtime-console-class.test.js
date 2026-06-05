#!/usr/bin/env node
// _handleOutput tags state.consoleClass from the claude TUI footer, and ONLY for
// claude-code (non-claude runtimes keep their own native turn detectors -> null).
import assert from "node:assert/strict";
import { TerminalProcessManager } from "../terminal-runtime.js";

const mgr = new TerminalProcessManager({ onOutput: async () => {} });

const claude = { id: "t1", runtime: "claude-code", agentId: "a1", outputTail: "" };
mgr.terminals.set("t1", claude);
await mgr._handleOutput("t1", claude, "✻ Crunched for 2m 3s (esc to interrupt)");
assert.equal(mgr.stateFor("t1").consoleClass, "working");

await mgr._handleOutput("t1", claude, "\r\n│ > │\n  ? for shortcuts\n");
assert.equal(mgr.stateFor("t1").consoleClass, "idle");

// Non-claude runtimes are never classified (null).
const codex = { id: "t2", runtime: "codex", agentId: "a2", outputTail: "" };
mgr.terminals.set("t2", codex);
await mgr._handleOutput("t2", codex, "✻ Crunched for 2m 3s (esc to interrupt)");
assert.equal(mgr.stateFor("t2").consoleClass, null);

console.log("terminal-runtime-console-class.test.js: all assertions passed");
