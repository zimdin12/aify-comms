// A handle this bridge kept across an aify-env restart must not match somebody else's process.
//
// THIS BRIDGE HOLDS HANDLES ACROSS AN OUTAGE ON PURPOSE. `delegated-exit.mjs` explains why: when the
// output stream ends and aify-env cannot be asked whether the process survived, the terminal is HELD
// rather than reported stopped, because a stale row heals and an orphaned process does not. So during
// an aify-env restart this bridge keeps `state.envProcessId` and, when the environment comes back,
// asks `processStillListed` whether that process is still there.
//
// THE DEFECT, proven against two real daemons on 2026-08-29 before the fix existed. aify-env's id
// counter lived at module scope and reset to zero every boot:
//
//     instance 1 (pid 113896)   agent-A -> p1, pid 136412
//     instance 2 (pid  91856)   agent-B -> p1, pid 67432
//
// So the question came back YES about a stranger, and three things here act on that answer:
// `reattachLostStreams` pipes agent-B's output into agent-A's terminal and console,
// `label-reconciler` writes agent-A's name onto agent-B's row -- the AGENT column an operator reads to
// tell them apart -- and `terminal-runtime.stop()` calls stop on agent-B's process when somebody stops
// agent-A. The first is a leak, the third is a cross-agent kill.
//
// FIXED IN aify-env (`lib/process-registry.mjs`), because process identity is its concern: a handle is
// now `<instance>-p<n>`, so a stale one cannot match anywhere, in any consumer, with no consumer
// change. This file is the CONSUMER's proof of that, because a fix proven only in the repo that made
// it leaves the question this bridge actually asks unproven -- and this bridge is the caller whose
// behaviour was wrong.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { EnvClient } from "../env-client.mjs";
import { delegatedExitVerdict, processStillListed } from "../delegated-exit.mjs";
import { sealedChildEnv } from "./_child-env.mjs";

const AIFY_ENV = process.env.AIFY_ENV_REPO || path.join(os.homedir(), "projects", "aify-env");
const DAEMON = path.join(AIFY_ENV, "bin", "aify-env.mjs");
const available = fs.existsSync(DAEMON);
const NL = String.fromCharCode(10);
const BACKSLASH = String.fromCharCode(92);

function startDaemon() {
  return new Promise((resolve, reject) => {
    // THE RECORD IS SEALED TO A TEMP FILE. aify-env reaps from the record at startup and an unset
    // `AIFY_ENV_PROCESS_RECORD` defaults to `~/.aify/env-processes.json`, the LIVE instance's --
    // which is how a test in this directory killed the operator's fleet three times in one evening.
    // It matters doubly here, where the whole point is to run two instances in sequence.
    const record = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "aify-handle-rec-")), "owned.json");
    const child = spawn(process.execPath, [DAEMON, "--port", "0"], {
      stdio: ["ignore", "pipe", "pipe"],
      env: sealedChildEnv({ AIFY_ENV_PROCESS_RECORD: record }),
    });
    let output = "";
    const timer = setTimeout(() => reject(new Error(`aify-env did not start:${NL}${output}`)), 20_000);
    child.stdout.on("data", (chunk) => {
      output += chunk;
      const match = /listening on (http:\/\/127\.0\.0\.1:\d+)/.exec(output);
      if (match) { clearTimeout(timer); resolve({ child, base: match[1] }); }
    });
    child.stderr.on("data", (chunk) => { output += chunk; });
    child.on("error", reject);
  });
}

const stopDaemon = (child) => new Promise((resolve) => {
  child.on("exit", resolve);
  child.kill();
});

/** A launcher aify-env will accept: it carries the contract marker and stays alive to be listed. */
function writeLauncher(dir, name) {
  const file = path.join(dir, name);
  fs.writeFileSync(file, ["#!/bin/bash", 'HARNESS_WRAPPER_VERSION="0.6.0"', "sleep 30", ""].join(NL));
  fs.chmodSync(file, 0o755);
  return file.split(BACKSLASH).join("/");
}

test("aify-env is checked out, so this can be exercised at all", () => {
  // FAILS rather than skips, like its siblings here. A cross-repo proof that did not run must not read
  // as green -- run-all.mjs names skipped files for exactly that reason.
  assert.equal(available, true, `aify-env not found at ${AIFY_ENV}. Set AIFY_ENV_REPO, or check it out.`);
});

test("A HANDLE FROM A DEAD INSTANCE IS NOT FOUND IN THE NEXT ONE'S LISTING", async (t) => {
  if (!available) return t.skip("aify-env is not checked out");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-handle-"));
  const first = await startDaemon();
  let stale;
  try {
    const started = await new EnvClient({ endpoint: first.base })
      .start({ service: "aify-comms", launcher: writeLauncher(dir, "agent-A-aify"), args: [], label: "agent-A" });
    assert.equal(started.ok, true, `start failed: ${started.error}`);
    stale = started.handle.id;
  } finally {
    await stopDaemon(first.child);
  }

  const second = await startDaemon();
  try {
    const client = new EnvClient({ endpoint: second.base });
    const fresh = await client
      .start({ service: "aify-comms", launcher: writeLauncher(dir, "agent-B-aify"), args: [], label: "agent-B" });
    assert.equal(fresh.ok, true, `start failed: ${fresh.error}`);

    // THE CONTROL FIRST. A probe that answers "not listed" about everything would pass the assertion
    // below while proving nothing, and this repo has produced that wrong zero more than once.
    assert.equal(
      await processStillListed(client, fresh.handle.id), true,
      "the probe cannot find a process that IS there, so its no below means nothing",
    );

    assert.equal(
      await processStillListed(client, stale), false,
      `the handle ${stale} from the previous instance was found in this one's listing. That yes is `
        + "what re-attached one agent's output into another's terminal, wrote the wrong name into the "
        + "AGENT column, and made a stop kill a process nobody asked to stop",
    );

    // WHAT THE CONSUMER DOES WITH THAT ANSWER, which is the half a listing check does not cover: given
    // `stillListed: false`, the terminal must be finalised rather than held open forever pointing at
    // an instance that no longer exists.
    //
    // NARROWED AFTER REVIEW. This said the old process was "genuinely gone", and this file does not
    // observe that -- it never retains the child pid across the daemon's shutdown. That the process
    // dies rests on aify-env's shutdown-and-boot-reap contract, which is aify-env's to prove, not a
    // thing measured here. What IS measured is that the handle does not match, which is the whole
    // claim this file is entitled to make.
    const verdict = delegatedExitVerdict({ observedExitFrame: false, stillListed: false });
    assert.equal(verdict.finalise, true);
    assert.equal(verdict.kind, "exited");
  } finally {
    await stopDaemon(second.child);
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("the two instances really were different, or the test above is vacuous", async (t) => {
  if (!available) return t.skip("aify-env is not checked out");
  // A SECOND CONTROL, and not a redundant one: if `startDaemon` had somehow returned the same daemon
  // twice, every assertion above would hold for the wrong reason. Two pids and two instance ids.
  const first = await startDaemon();
  const firstHealth = await new EnvClient({ endpoint: first.base }).health();
  await stopDaemon(first.child);
  const second = await startDaemon();
  const secondHealth = await new EnvClient({ endpoint: second.base }).health();
  await stopDaemon(second.child);

  const a = firstHealth.handle;
  const b = secondHealth.handle;
  assert.notEqual(a.pid, b.pid, "the same daemon answered twice");
  assert.ok(a.instance, "aify-env does not report which instance it is, so a consumer cannot say "
    + "\"your handle is from an older instance\" rather than \"that process is gone\"");
  assert.notEqual(a.instance, b.instance, "two boots reported the same instance id, which restores "
    + "the collision exactly");
});
