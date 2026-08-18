#!/usr/bin/env node
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
import { descendantPids, terminateProcessTree } from "../runtimes.js";

function isAlive(pid) {
  try {
    process.kill(pid, 0);
    const state = spawnSync("ps", ["-o", "stat=", "-p", String(pid)], { encoding: "utf8" });
    if (state.status === 0 && String(state.stdout || "").trim().startsWith("Z")) return false;
    return true;
  } catch {
    return false;
  }
}

async function waitForExitOrDead(proc, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!isAlive(proc.pid)) return;
    if (proc.exitCode !== null || proc.signalCode !== null) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

if (process.platform === "win32") {
  console.log("process-tree.test.js: skipped on win32");
  process.exit(0);
}

async function runCase(name, childOptions) {
  const parent = spawn(process.execPath, [
    "-e",
    `
      const { spawn } = require("node:child_process");
      const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], ${childOptions});
      console.log(child.pid);
      setInterval(() => {}, 1000);
    `,
    // `windowsHide` on BOTH the parent here and the child below (see the call sites). A detached
    // Windows child without it gets its own console, and when Windows Terminal is the default console
    // host that surfaces as a VISIBLE TAB — the operator watched one appear mid-suite, carrying a
    // launch error, and reasonably asked what was spawning it. The sibling
    // `hermes-daemon-default-killtree.test.js` already passes windowsHide on its detached spawns; this
    // file did not, which is the whole difference. It changes nothing the test asserts: the process
    // tree is the subject, not the window.
  ], { stdio: ["ignore", "pipe", "ignore"], windowsHide: true });

  const chunks = [];
  parent.stdout.on("data", (chunk) => chunks.push(chunk));
  await once(parent.stdout, "data");
  const childPid = Number(Buffer.concat(chunks).toString("utf8").trim().split(/\s+/)[0]);

  assert.ok(Number.isInteger(childPid) && childPid > 0, `${name}: child pid should be printed`);
  assert.ok(descendantPids(parent.pid).includes(childPid), `${name}: descendantPids should include spawned child`);

  terminateProcessTree(parent);
  await waitForExitOrDead(parent);
  await new Promise((resolve) => setTimeout(resolve, 250));

  assert.equal(isAlive(parent.pid), false, `${name}: parent process should be terminated`);
  assert.equal(isAlive(childPid), false, `${name}: child process should be terminated with parent tree`);
}

await runCase("same process group child", '{ stdio: "ignore", windowsHide: true }');
await runCase("detached child", '{ detached: true, stdio: "ignore", windowsHide: true }');

console.log("process-tree.test.js: all assertions passed");
