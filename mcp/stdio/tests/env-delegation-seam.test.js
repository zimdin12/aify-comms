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
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/**
 * A real launcher on disk: shebang plus the marker aify-env requires.
 *
 * These tests used `process.execPath` -- node itself -- which resolves fine and is NOT a launcher.
 * aify-env would have refused it, so the tests were passing on a spawn the environment could never
 * accept, and that gap is exactly where the Windows .cmd-shim defect hid. A fixture that satisfies the
 * real contract is what makes these tests mean something.
 */
function writeLauncherFixture(name) {
  const dir = mkdtempSync(join(tmpdir(), "aify-launcher-"));
  const file = join(dir, name);
  const eol = String.fromCharCode(10);
  writeFileSync(file, ["#!/usr/bin/env bash", 'HARNESS_WRAPPER_VERSION="0.6.0"', "exit 0", ""].join(eol));
  return file;
}

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

test("delegation ON with no argv refuses, and says why splitting the string is not the answer", async () => {
  // The seam used to refuse outright because the term shim did not exist. It does now, so the refusal
  // that remains is the honest one: a row carrying only a command STRING cannot be delegated, because
  // aify-env runs an allowlisted launcher file and splitting a shell string is the quoting bug this
  // design exists to avoid.
  const manager = new DispatchSpy({ envDelegation: { isEnabled: () => true } });
  await assert.rejects(() => manager.start(spec("seam-3")), (error) => {
    assert.match(error.message, /aify-env/i);
    assert.match(error.message, /argv/i, "the refusal must name what is missing");
    assert.match(
      error.message,
      /AIFY_COMMS_DELEGATE_SPAWNS/,
      "an operator who hits this must be told which half to turn off",
    );
    return true;
  });
});

test("delegation ON with argv whose launcher does not resolve refuses, rather than inventing a path", async () => {
  // aify-env is asked for a launcher BY PATH; it deliberately does not search PATH on our behalf. A
  // guess here would ask the environment to execute something we could not name.
  const manager = new DispatchSpy({ envDelegation: { isEnabled: () => true } });
  await assert.rejects(
    () => manager.start({ ...spec("seam-4"), argv: ["definitely-not-on-this-path-xyz", "--flag"] }),
    (error) => {
      assert.match(error.message, /does not resolve/i);
      return true;
    },
  );
});

test("delegation ON with a RESOLVABLE launcher goes to aify-env, not to a local spawn", async () => {
  // The path this phase was built for. Nothing spawns locally, and the manager asks the environment.
  const asked = [];
  const client = {
    async start(req) { asked.push(req); return { ok: true, handle: { id: "env-1", pid: 4242, terminal: true } }; },
    async subscribeOutput() { return () => {}; },
  };
  const manager = new DispatchSpy({ envDelegation: { isEnabled: () => true, client } });
  const result = await manager.start({ ...spec("seam-5"), argv: [writeLauncherFixture("probe-aify"), "--version"] });

  assert.equal(result.pid, 4242);
  assert.equal(asked.length, 1, "the environment was not asked to start anything");
  assert.equal(asked[0].service, "aify-comms");
  assert.deepEqual(asked[0].args, ["--version"], "argv[0] is the launcher; the rest are its arguments");
  assert.equal(manager.reached, null, "it dispatched locally as well as delegating");
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
