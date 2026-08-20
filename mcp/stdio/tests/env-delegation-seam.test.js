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
  assert.equal(isEnabled(process.env), false, "AIFY_COMMS_DELEGATE_SPAWNS is set here; delegation is not off");
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

// ── the production wiring ────────────────────────────────────────────────────────
// Every test above INJECTS an envDelegation, which is exactly why none of them could see that the
// production manager was constructed without one. The constructor defaults it to null, so the branch
// never fired and setting the environment variable did nothing at all — a placebo flag.
//
// The lesson generalises: a unit test of a seam cannot see a gap at the call site, because the test IS
// the call site.

test("the PRODUCTION manager is wired to a real delegation, not left at the default", async () => {
  const { TERMINAL_MANAGER } = await import("../terminal-manager.mjs");
  assert.notEqual(TERMINAL_MANAGER.envDelegation, null, "the flag is a placebo: nothing consults it");
  assert.equal(typeof TERMINAL_MANAGER.envDelegation.isEnabled, "function");
});

test("the production wiring reads the REAL environment, and here that is OFF", async () => {
  const { TERMINAL_MANAGER } = await import("../terminal-manager.mjs");
  assert.equal(
    TERMINAL_MANAGER.envDelegation.isEnabled(),
    false,
    "delegation is enabled on this machine; managed spawns would refuse",
  );
});

test("the production wiring reads the environment at CALL time, not at construction", async () => {
  // A value captured when the module loaded would ignore anything exported afterwards, which is how a
  // flag becomes untestable and, worse, unturnoffable in a running process.
  const { TERMINAL_MANAGER } = await import("../terminal-manager.mjs");
  const before = process.env.AIFY_COMMS_DELEGATE_SPAWNS;
  const beforeEndpoint = process.env.AIFY_ENV_ENDPOINT;
  try {
    process.env.AIFY_COMMS_DELEGATE_SPAWNS = "1";
    // Set, and reachable by nothing. Never a real environment.
    process.env.AIFY_ENV_ENDPOINT = "http://127.0.0.2:1";
    assert.equal(TERMINAL_MANAGER.envDelegation.isEnabled(), true, "the value was captured at load");
  } finally {
    if (before === undefined) delete process.env.AIFY_COMMS_DELEGATE_SPAWNS;
    else process.env.AIFY_COMMS_DELEGATE_SPAWNS = before;
    if (beforeEndpoint === undefined) delete process.env.AIFY_ENV_ENDPOINT;
    else process.env.AIFY_ENV_ENDPOINT = beforeEndpoint;
  }
  assert.equal(TERMINAL_MANAGER.envDelegation.isEnabled(), false, "the seal did not restore");
});

// The refusal has to name the half an operator should actually change.
//
// `isEnabled` needs BOTH `AIFY_COMMS_DELEGATE_SPAWNS` and `AIFY_ENV_ENDPOINT`. The refusal was
// written against the FIRST design, which keyed on the endpoint alone -- the design that was
// deliberately rejected, because that variable is what aify-env's own doctor and TUI read to find the
// daemon.
//
// So the message told an operator to unset the one variable that is ALSO their diagnostic tooling's
// only way to find the environment. Following it works, and costs them their doctor.

test("the refusal names the OPT-IN flag, not the endpoint an operator also needs for doctor", async () => {
  const { TerminalProcessManager } = await import("../terminal-runtime.js");
  const manager = new TerminalProcessManager({ envDelegation: { isEnabled: () => true } });

  const error = await manager
    .start({ id: "seam-message", command: "true", cwd: process.cwd() })
    .then(() => null, (e) => e);

  assert.notEqual(error, null, "delegation was on and the seam did not refuse");
  assert.match(
    error.message,
    /AIFY_COMMS_DELEGATE_SPAWNS/,
    "the refusal does not name the opt-in flag, so an operator cannot tell what to turn off",
  );
});

test("the refusal does not tell an operator to unset the endpoint aify-env's doctor reads", async () => {
  const { TerminalProcessManager } = await import("../terminal-runtime.js");
  const manager = new TerminalProcessManager({ envDelegation: { isEnabled: () => true } });

  const error = await manager
    .start({ id: "seam-remedy", command: "true", cwd: process.cwd() })
    .then(() => null, (e) => e);

  assert.notEqual(error, null, "delegation was on and the seam did not refuse");
  assert.doesNotMatch(
    error.message,
    /Unset it to spawn locally/,
    "the remedy points at AIFY_ENV_ENDPOINT, which is also how aify-env's doctor and TUI are found",
  );
});
