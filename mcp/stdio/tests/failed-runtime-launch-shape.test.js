// What a FAILED runtime launch still has to hand back, and the adapter identity every runtime inherits.
//
// Seventeenth cluster off the V8-coverage census: `runtimes.js`'s `interrupt` and `steer` (the two controls
// inside `failedRuntimeController`) and `adapters/base.js`'s `get displayName`, all zero-count.
//
// WHY THE FAILURE SHAPE MATTERS. `launchRuntimeRun` never throws at its caller - it returns the legacy shape
// `{ capabilities, interrupt, steer, promise }` even when nothing could be launched, with the failure carried
// on `promise`. The dispatch loop then treats that object like any other controller: it may call `interrupt`
// when the operator presses Stop on a run that never started. If the failure path returned a bare object or a
// throwing interrupt, a Stop on a failed dispatch would fault inside the control loop rather than resolving -
// which is how one bad dispatch takes out the loop that serves every other agent.
//
// A DECLINED CLUSTER, recorded here because the reason is not "no time". `managed-teardown-sweeps.mjs`'s
// `fetchOwnership`/`readMarkers` and `single-agent-teardown.mjs`'s `readMarkers` are also zero-count, and both
// are the DEFAULT WIRING of a reaper: reaching them means either setting AIFY_ENVIRONMENT_BRIDGE (which once
// made a test process supersede the live bridge and reap seven gateway hosts) or running a real process scan
// with real killers against the operator's own machine. The enumeration logic they wire up is separately
// tested with fakes; the wiring itself is not worth that blast radius.

import assert from "node:assert/strict";
import test from "node:test";

import { launchRuntimeRun, controlCapabilitiesForRuntime } from "../runtimes.js";
import { adapterFor } from "../adapters/index.js";
import { RuntimeAdapter } from "../adapters/base.js";

const RUNTIMES = ["claude-code", "codex", "hermes", "pi", "opencode"];

// ── the failed-launch shape ─────────────────────────────────────────────────

test("an unknown runtime yields a USABLE controller shape, not a throw", async () => {
  const shape = launchRuntimeRun({ agentId: "a", agentInfo: { runtime: "not-a-runtime" }, run: {} });

  assert.equal(typeof shape.interrupt, "function", "a Stop on this run would fault in the control loop");
  assert.equal(typeof shape.steer, "function");
  assert.ok(shape.promise && typeof shape.promise.then === "function");
  await assert.rejects(() => shape.promise, /not-a-runtime/,
    "the failure must name the runtime that could not be launched");
});

test("the failed shape's interrupt is a silent no-op", async () => {
  // There is no turn to cancel, and the operator did press Stop. Reporting an error for a run that never
  // started tells them something is broken when the only thing wrong is what they already knew.
  const shape = launchRuntimeRun({ agentId: "a", agentInfo: { runtime: "not-a-runtime" }, run: {} });
  assert.equal(shape.interrupt(), undefined);
  assert.doesNotThrow(() => shape.interrupt());
  await shape.promise.catch(() => {});
});

test("the failed shape's steer REJECTS, and names the runtime", async () => {
  // Opposite of interrupt, deliberately: a steer that cannot be delivered has to fail, or the caller believes
  // its text reached the agent.
  const shape = launchRuntimeRun({ agentId: "a", agentInfo: { runtime: "not-a-runtime" }, run: {} });
  await assert.rejects(async () => shape.steer("text"), /not-a-runtime/);
  await assert.rejects(async () => shape.steer("text"), /does not support active dispatch/);
  await shape.promise.catch(() => {});
});

test("the failed shape still declares the runtime's capabilities", async () => {
  // Read by callers that decide whether to offer a control at all. An unknown runtime resolves to no
  // capabilities rather than to undefined, which would read as "not yet known".
  const shape = launchRuntimeRun({ agentId: "a", agentInfo: { runtime: "not-a-runtime" }, run: {} });
  assert.deepEqual(shape.capabilities, { interrupt: false, steer: false });
  await shape.promise.catch(() => {});
});

test("a runtime that cannot serve THIS execution mode fails the same way", async () => {
  // Pi is the live example: `omp --mode rpc` is single-client stdio, so PiAdapter.controllerFor returns null
  // for resident. The failure has to arrive as a rejected promise with a usable shape around it, exactly like
  // an unknown runtime - this is the path a real misconfigured agent takes.
  const shape = launchRuntimeRun({
    agentId: "a",
    agentInfo: { runtime: "pi", sessionMode: "resident" },
    run: { executionMode: "resident" },
  });

  assert.equal(typeof shape.interrupt, "function");
  assert.equal(shape.interrupt(), undefined);
  await assert.rejects(() => shape.promise, /pi/);
  await assert.rejects(() => shape.promise, /resident/,
    "the failure does not say WHICH execution mode was refused");
  assert.deepEqual(shape.capabilities, controlCapabilitiesForRuntime("pi"),
    "a mode-refusal must still report the runtime's real capabilities");
  await assert.rejects(async () => shape.steer("text"), /does not support active dispatch/);
});

// ── adapter identity ────────────────────────────────────────────────────────

test("every runtime adapter presents a human display name", () => {
  // MEASURED, against my own expectation: I wrote this asserting displayName EQUALS name, because the base
  // class's `get displayName() { return this.name; }` is the default. Every shipped adapter overrides it - the
  // name is a wire key ("claude-code"), the display name is for people ("Claude Code"). Pinned exactly, because
  // these strings are operator-facing and a silent drift to the wire key is the kind of regression nobody files.
  assert.deepEqual(
    Object.fromEntries(RUNTIMES.map((runtime) => [runtime, adapterFor(runtime).displayName])),
    {
      "claude-code": "Claude Code",
      codex: "Codex",
      hermes: "Hermes",
      pi: "Pi",
      opencode: "OpenCode",
    },
  );
  for (const runtime of RUNTIMES) {
    assert.equal(adapterFor(runtime).name, runtime, `${runtime}: the wire key changed`);
  }
});

test("the base-class default IS the name, for an adapter that does not override it", () => {
  // The default still has to work: a future adapter that ships without a display name gets its key rather than
  // `undefined` in whatever UI renders it.
  class Minimal extends RuntimeAdapter {
    get name() { return "minimal"; }
  }
  assert.equal(new Minimal().displayName, "minimal");
});

test("a BLANK name is passed through, not papered over with a placeholder", () => {
  // The getter deliberately has no `|| "unknown"` fallback. A blank identity is a misconfigured adapter, and it
  // should look blank wherever it renders — a placeholder makes a broken adapter indistinguishable from one
  // whose name simply is not known yet, which is the whole no-evidence-is-not-a-pass problem in miniature.
  class Blank extends RuntimeAdapter {
    get name() { return ""; }
  }
  assert.equal(new Blank().displayName, "");
});

test("the base class's name is abstract, so displayName cannot invent one", () => {
  // displayName reads `this.name`, and the base `name` throws. A subclass that forgot to declare a name must
  // fail loudly rather than present an adapter with an undefined identity.
  class Nameless extends RuntimeAdapter {}
  assert.throws(() => new Nameless().displayName, /abstract: subclass must override name/);
});

test("a subclass CAN give itself a display name distinct from its key", () => {
  // The reason the getter exists rather than a field: the wire name is a key ("claude-code"), the display name
  // is for humans. Pinning that the override path works keeps the indirection honest.
  class Pretty extends RuntimeAdapter {
    get name() { return "wire-key"; }
    get displayName() { return "Wire Key"; }
  }
  const adapter = new Pretty();
  assert.equal(adapter.name, "wire-key");
  assert.equal(adapter.displayName, "Wire Key");
});
