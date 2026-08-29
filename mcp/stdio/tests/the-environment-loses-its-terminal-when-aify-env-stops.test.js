// The advertised capability follows a REAL aify-env up and down, not a fake that always answers.
//
// THE OPERATOR'S QUESTION, asked twice: "why do i see agents available if their env is down. shouldnt
// they be offline?" `the-environment-advertises-the-tier-that-would-serve-it.test.js` proves the
// decision function. This file proves the CALL -- that `probeEnvTerminal` asks a process that can
// actually stop answering, and that the verdict flips when it does.
//
// WHY BOTH. This repo has shipped a feature that could never fire, with six green tests, because all
// six tested the pure builder and none tested the call site. A capability that reports "no terminal"
// correctly for a fake and never notices a real daemon dying is the same defect with better manners.
//
// THE THIRD OBSERVATION IS THE ONE THAT MATTERS. Up, then down, then UP AGAIN: a probe that latched
// on first failure would pass the first two and leave the operator with a fleet stuck at offline after
// their environment came back. Costlier than the bug being fixed, and invisible to a two-state test.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { EnvClient } from "../env-client.mjs";
import { probeEnvTerminal, terminalCapability } from "../terminal-capability.mjs";
import { sealedChildEnv } from "./_child-env.mjs";

const AIFY_ENV = process.env.AIFY_ENV_REPO || path.join(os.homedir(), "projects", "aify-env");
const DAEMON = path.join(AIFY_ENV, "bin", "aify-env.mjs");
const available = fs.existsSync(DAEMON);

function startDaemon() {
  return new Promise((resolve, reject) => {
    // THE RECORD IS SEALED TO A TEMP FILE. aify-env reaps from the record at startup, and an unset
    // `AIFY_ENV_PROCESS_RECORD` defaults to `~/.aify/env-processes.json` -- the LIVE instance's. Its
    // own guard does not save it: it reaps once it HOLDS THE PORT, and `--port 0` is always free. Two
    // sibling files in this directory carry the same seal, and one of them killed the operator's fleet
    // three times in one evening before it did.
    const record = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "aify-capability-rec-")), "owned.json");
    const child = spawn(process.execPath, [DAEMON, "--port", "0"], {
      stdio: ["ignore", "pipe", "pipe"],
      env: sealedChildEnv({ AIFY_ENV_PROCESS_RECORD: record }),
    });
    let output = "";
    const timer = setTimeout(() => reject(new Error(`aify-env did not start:\n${output}`)), 20_000);
    child.stdout.on("data", (chunk) => {
      output += chunk;
      const match = /listening on (http:\/\/127\.0\.0\.1:\d+)/.exec(output);
      if (match) {
        clearTimeout(timer);
        resolve({ child, base: match[1] });
      }
    });
    child.stderr.on("data", (chunk) => { output += chunk; });
    child.on("error", reject);
  });
}

const stopDaemon = (child) => new Promise((resolve) => {
  child.on("exit", resolve);
  child.kill();
});

/** The delegation shape the bridge hands the probe: enabled, with a client pointed at an endpoint. */
const delegationTo = (endpoint) => ({ isEnabled: () => true, client: new EnvClient({ endpoint }) });

test("aify-env is checked out, so this can be exercised at all", () => {
  // FAILS rather than skips, like its siblings. "The cross-repo behaviour is unverified" must not read
  // as green -- run-all.mjs names skipped files for exactly that reason, and a file that skipped its
  // only real assertion has proven nothing about the thing the operator asked about.
  assert.equal(available, true, `aify-env not found at ${AIFY_ENV}. Set AIFY_ENV_REPO, or check it out.`);
});

test("a live aify-env advertises a terminal, a dead one does not, and a revived one does again", async (t) => {
  if (!available) return t.skip("aify-env is not checked out");
  const { child, base } = await startDaemon();
  const delegation = delegationTo(base);

  const up = await probeEnvTerminal(delegation);
  assert.equal(up.terminal, true, "a running aify-env with terminals available must answer yes");
  assert.ok(Array.isArray(up.processes), "and must hand back its process list, not null");
  assert.equal(
    terminalCapability({ delegationEnabled: true, envHealthy: up.terminal, localTerminal: false }).terminal,
    true,
    "the bridge's own node-pty is false here on purpose: under delegation it must not be consulted, "
      + "and a capability that still said yes would be reading the tier that no longer spawns",
  );

  await stopDaemon(child);

  const down = await probeEnvTerminal(delegation);
  assert.equal(down.terminal, null, "a dead aify-env is UNKNOWN, which is not the same as a `false` it "
    + "actually reported; the caller has to be able to tell those apart");
  const verdict = terminalCapability({
    delegationEnabled: true, envHealthy: down.terminal, localTerminal: true,
  });
  assert.equal(
    verdict.terminal, false,
    "SILENCE IS NOT YES. With aify-env down and the bridge's own node-pty loaded, the old code "
      + "advertised a terminal and 20 managed agents read `available` -- a promise of cold-start that "
      + "every send would have broken",
  );
  assert.match(verdict.reason, /aify-env/, "and the reason must name the tier that did not answer, or "
    + "an operator reading the row has to guess which of three processes is down");

  // UP AGAIN. The revival case is the one a latching probe passes the first two halves of.
  const again = await startDaemon();
  try {
    const revived = await probeEnvTerminal(delegationTo(again.base));
    assert.equal(revived.terminal, true, "the capability did not come back when aify-env did");
  } finally {
    await stopDaemon(again.child);
  }
});
