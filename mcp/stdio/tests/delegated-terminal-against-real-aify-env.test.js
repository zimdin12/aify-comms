#!/usr/bin/env node
// A DELEGATED terminal, driven through the real manager against a real aify-env.
//
// Every other test of this path stops at a fake: the manager talks to a stand-in client, or the client
// talks to a stand-in server. Both halves are green in that arrangement and the pair can still be
// broken -- which is the failure CONNECTION_TRACE.md names as the shape every defect in this program
// has had.
//
// WHAT THIS PROVES, and it is the evidence the flip needs: a terminal started through
// TerminalProcessManager with delegation ON reaches aify-env, its output arrives through the manager's
// ordinary onOutput path, and its exit arrives through onExit. Those two callbacks are where batching,
// auto-answer, classification and the heal path live, so output and exit landing there is what makes a
// delegated agent behave like a local one rather than merely start like one.
//
// It does NOT touch the fleet: its own daemon on an ephemeral port, its own launcher in a temp
// directory, and the flag set only on the manager instance it builds.

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { EnvClient } from "../env-client.mjs";
import { TerminalProcessManager } from "../terminal-runtime.js";
import { sealedChildEnv } from "./_child-env.mjs";

const AIFY_ENV = process.env.AIFY_ENV_REPO || path.join(os.homedir(), "projects", "aify-env");
const DAEMON = path.join(AIFY_ENV, "bin", "aify-env.mjs");
const available = fs.existsSync(DAEMON);
const LF = String.fromCharCode(10);

function startDaemon() {
  return new Promise((resolve, reject) => {
    // The record is SEALED to a temp file: aify-env reaps from it at startup, and pointed at the real
    // one a test could kill a process it never started.
    const record = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "aify-deleg-rec-")), "owned.json");
    const child = spawn(process.execPath, [DAEMON, "--port", "0"], {
      stdio: ["ignore", "pipe", "pipe"],
      // SEALED. Spreading process.env here would hand the daemon the operator's service URL, API key,
      // hermes session and agent identity on any machine where a wrapper has set them -- and pass
      // silently everywhere they happen to be unset, which is every developer shell and no wrapper.
      env: sealedChildEnv({ AIFY_ENV_PROCESS_RECORD: record }),
    });
    let out = "";
    const timer = setTimeout(() => reject(new Error(`aify-env did not start: ${out}`)), 20_000);
    child.stdout.on("data", (c) => {
      out += c;
      const m = /listening on (http:\/\/127\.0\.0\.1:\d+)/.exec(out);
      if (m) { clearTimeout(timer); resolve({ child, base: m[1] }); }
    });
    child.on("error", reject);
  });
}

const writeLauncher = (dir, name, lines) => {
  const file = path.join(dir, name);
  fs.writeFileSync(file, ["#!/bin/bash", 'HARNESS_WRAPPER_VERSION="0.6.0"', ...lines, ""].join(LF));
  return file;
};

test("a delegated terminal reaches aify-env, and its output and exit come back through the manager", {
  skip: !available && `aify-env is not checked out at ${AIFY_ENV}; set AIFY_ENV_REPO`,
}, async () => {
  const { child, base } = await startDaemon();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-deleg-"));
  const launcher = writeLauncher(dir, "probe-aify", ['echo "DELEGATED-OUTPUT"', "exit 5"]);

  const output = [];
  let exited = null;
  const manager = new TerminalProcessManager({
    onOutput: async (_id, text) => { output.push(text); },
    onExit: async (_id, info) => { exited = info; },
    envDelegation: { isEnabled: () => true, client: new EnvClient({ endpoint: base }) },
  });

  try {
    const started = await manager.start({
      id: "delegated-1",
      command: `${launcher}`,
      argv: [launcher],
      cwd: dir,
      runtime: "claude-code",
    });

    assert.ok(started.pid > 0, "aify-env reported no pid for the delegated terminal");

    // Output and exit arrive over a stream, so they are waited FOR rather than waited out.
    const deadline = Date.now() + 15_000;
    while (Date.now() < deadline && exited === null) {
      await new Promise((r) => setTimeout(r, 100));
    }

    assert.match(output.join(""), /DELEGATED-OUTPUT/, "the agent's output never reached onOutput");
    assert.notEqual(exited, null, "the agent exited and onExit was never called");
    assert.equal(exited.code, 5, "the exit CODE was lost; healing decisions are made from it");
  } finally {
    await manager.stop("delegated-1").catch(() => {});
    child.kill("SIGKILL");
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("a delegated terminal can be TYPED AT, and the agent sees it", {
  skip: !available && `aify-env is not checked out at ${AIFY_ENV}; set AIFY_ENV_REPO`,
}, async () => {
  // Without this a delegated console is a viewer. The keystroke has to arrive at the process, which is
  // only observable by what the process echoes back.
  const { child, base } = await startDaemon();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-deleg-in-"));
  const launcher = writeLauncher(dir, "echo-aify", ["read -r line", 'echo "SAW:$line"']);

  const output = [];
  const manager = new TerminalProcessManager({
    onOutput: async (_id, text) => { output.push(text); },
    envDelegation: { isEnabled: () => true, client: new EnvClient({ endpoint: base }) },
  });

  try {
    await manager.start({ id: "delegated-2", command: launcher, argv: [launcher], cwd: dir, runtime: "claude-code" });
    await new Promise((r) => setTimeout(r, 800));

    const terminal = manager.terminals.get("delegated-2");
    assert.ok(terminal?.term, "the delegated terminal has no term to type into");
    terminal.term.write(`hello-there${LF}`);

    const deadline = Date.now() + 12_000;
    while (Date.now() < deadline && !output.join("").includes("SAW:")) {
      await new Promise((r) => setTimeout(r, 100));
    }
    assert.match(output.join(""), /SAW:hello-there/, "the keystroke never reached the delegated agent");
  } finally {
    await manager.stop("delegated-2").catch(() => {});
    child.kill("SIGKILL");
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
