#!/usr/bin/env node
// A delegated spawn must not hand aify-env the bridge's own identity.
//
// `child-env-hygiene.mjs` exists because a bridge's ancestry reaches everything it starts, and it was
// discovered one symptom at a time: a bridge's AIFY_AGENT_ROLE becoming every worker's role, a
// CLAUDE_CODE_CHILD_SESSION marker disabling transcripts for every managed agent. `terminalChildEnv`
// strips the list, and every spawn used to go through it.
//
// DELEGATION MOVED THE SPAWN TO ANOTHER PROCESS. `startDelegated` POSTs an env to aify-env, which
// forwards it to its own spawn call. The env arriving there is clean today only because the delegated
// branch sits downstream of `terminalChildEnv` — an accident of ordering in one call path, not a
// property of the boundary. A second delegated call site that assembled its own env would leak the
// bridge's identity with nothing to report it, and the symptom would be an agent answering to the
// wrong name rather than an error.
//
// So the strip is applied AT the boundary, and this asserts it there rather than trusting the caller.
// The pass-through case matters just as much: normalising an absent env to `{}` would look tidy and
// would hand the child an environment with no PATH, because aify-env gives `{}` straight to spawn
// while `undefined` means inherit.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { NEVER_INHERITED } from "../child-env-hygiene.mjs";
import { TerminalProcessManager } from "../terminal-runtime.js";

/** A manager whose delegation client records the request instead of making it. */
function delegatingManager() {
  const sent = [];
  const manager = new TerminalProcessManager({
    onOutput: async () => {},
    envDelegation: {
      client: {
        start: async (request) => {
          sent.push(request);
          // Enough shape for startDelegated to continue past the call.
          return { ok: true, handle: { id: "p1" } };
        },
        watch: async () => ({ ok: true }),
      },
    },
  });
  return { manager, sent };
}

/** A launcher aify-env would accept: startDelegated refuses one without the wrapper marker. */
function writeLauncher() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-hygiene-"));
  const file = path.join(dir, "probe-aify");
  const LF = String.fromCharCode(10);
  const marker = "HARNESS_WRAPPER_VERSION=" + JSON.stringify("0.6.0");
  fs.writeFileSync(file, ["#!/bin/bash", marker, "exit 0", ""].join(LF));
  fs.chmodSync(file, 0o755);
  return file;
}

/** The spec a delegated start needs, with an env the caller has NOT cleaned. */
function dirtySpec(overrides = {}) {
  const launcher = writeLauncher();
  return {
    id: "term-1",
    command: `${launcher} --flag`,
    argv: [launcher, "--flag"],
    cwd: ".",
    env: {
      PATH: "/usr/bin",
      HOME: "/home/dev",
      AIFY_AGENT_ID: "the-bridge-itself",
      AIFY_AGENT_ROLE: "manager",
      CLAUDE_CODE_CHILD_SESSION: "1",
    },
    agentId: "worker-7",
    ...overrides,
  };
}

test("the fixture carries the markers the strip is supposed to remove", () => {
  // Positive control: every assertion below is an ABSENCE, and an absence proves nothing if the
  // fixture never had the thing. Tied to the real list so a renamed marker fails here first.
  const env = dirtySpec().env;
  const present = Object.keys(NEVER_INHERITED).filter((name) => name in env);
  assert.ok(present.length >= 3, `the fixture only carries ${present.join(", ")}`);
});

test("the delegated request carries none of the never-inherited markers", async () => {
  const { manager, sent } = delegatingManager();
  try {
    await manager.startDelegated(dirtySpec());
  } catch {
    // startDelegated does more than POST — launcher resolution, output watching. The assertion is
    // about what it SENT, and it either sent something or the next check reports that instead.
  }
  assert.equal(sent.length, 1, "the delegated start never reached the client");
  const env = sent[0].env ?? {};
  for (const name of Object.keys(NEVER_INHERITED)) {
    assert.ok(!(name in env), `${name} was forwarded to aify-env, so the worker inherits the bridge's ${name}`);
  }
});

test("everything the child legitimately needs still crosses", async () => {
  // The inverse failure, and the more likely one: a strip that took PATH with it would produce a
  // worker that cannot find its own runtime, which reads as a launcher bug rather than an env bug.
  const { manager, sent } = delegatingManager();
  try {
    await manager.startDelegated(dirtySpec());
  } catch { /* see above */ }
  assert.equal(sent.length, 1);
  assert.equal(sent[0].env.PATH, "/usr/bin");
  assert.equal(sent[0].env.HOME, "/home/dev");
});

test("an absent env stays absent rather than becoming an empty object", async () => {
  // aify-env forwards what it is given to spawn: `{}` is an environment with no PATH, `undefined`
  // means inherit. Normalising one into the other is the tidy-looking change that breaks a spawn.
  const { manager, sent } = delegatingManager();
  try {
    await manager.startDelegated(dirtySpec({ env: undefined }));
  } catch { /* see above */ }
  assert.equal(sent.length, 1);
  assert.equal(sent[0].env, undefined, "an absent env was normalised, which spawns without a PATH");
});
