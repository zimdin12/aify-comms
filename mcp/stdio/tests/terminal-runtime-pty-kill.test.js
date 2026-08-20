#!/usr/bin/env node
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { TerminalProcessManager } from "../terminal-runtime.js";
const child = spawn("sleep", ["120"], { detached: process.platform !== "win32", stdio: "ignore", windowsHide: true });
const mgr = new TerminalProcessManager({ onOutput: async () => {}, onExit: async () => {} });
mgr.terminals.set("t1", {
  id: "t1", kind: "pty", exitPromise: Promise.resolve(),
  term: { pid: child.pid, kill: (sig) => { try { process.kill(child.pid, sig); } catch {} } },
});
await mgr.stop("t1", "test");
await new Promise((r) => setTimeout(r, 400));
let alive = true;
try { process.kill(child.pid, 0); } catch { alive = false; }
assert.equal(alive, false, "child process group should be reaped by stop()");
console.log("terminal-runtime-pty-kill.test.js: all assertions passed");
