#!/usr/bin/env node
// The seam where spawning leaves aify-comms, and the proof that it is inert.
//
// THE TESTS THAT MATTER ARE THE FIRST TWO. With nothing configured, `start()` must dispatch to exactly
// the path it dispatched to before this branch existed. If that ever stops being true, deploying this
// file moves where every managed agent on the fleet is started.
//
// NOTHING IS SPAWNED HERE, and that is a deliberate test design rather than a shortcut. The seam is a
// DISPATCH decision, so dispatch is what gets asserted: the local methods are overridden to record
// that they were reached. Actually spawning would drag in a pty, a console keepalive and a teardown
// that raises "Signals not supported on windows" — three ways for this file to fail for reasons that
// have nothing to do with the branch under test.
//
// The remaining tests pin that a CONFIGURED-but-unfinished delegation refuses loudly instead of
// half-working. Half-working is the dangerous shape: a delegated process fed through _handleOutput and
// _handleExit inherits the batching, auto-answer and classification for free, so it would LOOK right,
// while write, resize, kill and the keepalive all still went to a local pty that does not exist. Agents
// that are subtly wrong in ways nobody can attribute are worse than agents that will not start.

import assert from "node:assert/strict";
import { test } from "node:test";

import { TerminalProcessManager } from "../terminal-runtime.js";
import { isEnabled } from "../env-client.mjs";

/**
 * A manager that records which local path it was dispatched to and starts nothing.
 *
 * Both overrides return the shape `start()`'s callers expect, so the seam is exercised exactly as it
 * is in production up to the point where a process would have appeared.
 */
class DispatchSpy extends TerminalProcessManager {
  reached = null;

  async startPty(spec) {
    this.reached = "pty";
    return { pid: 0, status: "spy", pty: true, spec };
  }

  async startPipeProcess(spec) {
    this.reached = "pipe";
    return { pid: 0, status: "spy", pty: false, spec };
  }
}

const spec = (id) => ({ id, command: "aify-seam-probe", cwd: process.cwd(), env: {} });

test("OFF by default in THIS environment: nothing is delegated", () => {
  // Read from the real environment rather than a fixture, because the claim is about this machine and
  // every machine that ships this file.
  assert.equal(isEnabled(process.env), false, "AIFY_ENV_ENDPOINT is set here; delegation is not off");
});

test("with no delegation configured, start() dispatches LOCALLY", async () => {
  const manager = new DispatchSpy();
  const result = await manager.start(spec("seam-1"));
  assert.ok(["pty", "pipe"].includes(manager.reached), `dispatched nowhere local: ${manager.reached}`);
  assert.equal(result.status, "spy");
});

test("a manager built with NO envDelegation has none — the default is not a live object", () => {
  // A default that quietly constructed a client would make the branch depend on the environment at
  // CONSTRUCTION time, which is a different and much worse thing than at start time.
  assert.equal(new TerminalProcessManager().envDelegation, null);
});

test("a delegation reporting DISABLED is consulted and not taken", async () => {
  let asked = 0;
  const manager = new DispatchSpy({ envDelegation: { isEnabled: () => { asked += 1; return false; } } });
  await manager.start(spec("seam-2"));
  assert.equal(asked, 1, "the branch did not consult the delegation at all");
  assert.ok(["pty", "pipe"].includes(manager.reached), "a disabled delegation blocked the local path");
});

test("a delegation reporting ENABLED refuses, and says what is missing", async () => {
  // Fail closed, loudly, naming the gap. The alternative is a flag whose "on" position produces agents
  // that are subtly different, which is the failure this phase exists to avoid introducing.
  const manager = new DispatchSpy({ envDelegation: { isEnabled: () => true } });
  await assert.rejects(() => manager.start(spec("seam-3")), (error) => {
    assert.match(error.message, /aify-env/i);
    assert.match(error.message, /shim|keepalive/i, "the refusal must name what is not delegated");
    assert.match(error.message, /PHASE8_STATUS/, "the refusal must point somewhere");
    return true;
  });
});

test("the refusal happens BEFORE any local dispatch", async () => {
  // A refusal after a spawn would leave a process nobody is tracking, which is the orphan class this
  // project has already paid for.
  const manager = new DispatchSpy({ envDelegation: { isEnabled: () => true } });
  await assert.rejects(() => manager.start(spec("seam-4")));
  assert.equal(manager.reached, null, "the refusal came after dispatching locally");
  assert.equal(manager.terminals.size, 0, "a refused start registered a terminal");
});

test("id and command are still validated FIRST, delegation or not", async () => {
  // Argument validation must not move behind a feature flag: a missing id is a caller bug either way
  // and should read the same on both paths.
  const manager = new DispatchSpy({ envDelegation: { isEnabled: () => true } });
  await assert.rejects(() => manager.start({ command: "x" }), /id is required/);
  await assert.rejects(() => manager.start({ id: "a" }), /command is required/);
  assert.equal(manager.reached, null);
});
