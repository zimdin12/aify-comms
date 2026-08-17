// CodexController: which implementation a codex dispatch is routed to, and whether the delivery verbs reach
// it.
//
// Eleventh cluster off the V8-coverage census, and the second of the eight controller families. `get
// terminalSink`, `injectMessage`, `interrupt`, `steer` and `setReadyListener` all had a zero call count.
//
// THREE DESTINATIONS, and the difference is where the turn actually runs:
//
//   * managed + managedViaWrapper -> a no-op. The wrapper's own child bridge claims and delivers; this
//     controller resolving anything else would be a second claimant for one run.
//   * managed without the wrapper -> CodexManagedController, the persistent CodexSession pool.
//   * resident / channel / anything unrecognised -> CodexLegacyController, the WS app-server path.
//
// Both real constructors are inert (they set fields and nothing else), so routing can be asserted without
// spawning a codex app-server or touching a CODEX_HOME.

import assert from "node:assert/strict";
import test from "node:test";

import { CodexController } from "../controllers/codex-controller.js";
import { CodexManagedController } from "../controllers/codex-managed-controller.js";
import { CodexLegacyController } from "../controllers/codex-legacy-controller.js";

const LIVE_APP_SERVER = { runtimeConfig: { appServerUrl: "ws://127.0.0.2:9999" } };
const NO_APP_SERVER = { runtimeConfig: {} };

const implFor = (opts) => new CodexController(opts)._impl;

async function startAndResolve(controller) {
  const shape = controller.start();
  return { shape, result: await shape.promise };
}

// ── the wrapper-delegated route ──────────────────────────────────────────────

test("managed + managedViaWrapper resolves as delegated and performs nothing", async () => {
  const controller = new CodexController({ executionMode: "managed", managedViaWrapper: true });
  const { shape, result } = await startAndResolve(controller);

  assert.deepEqual(shape.capabilities, { interrupt: false, steer: false });
  assert.equal(result.status, "delegated");
  assert.match(result.summary, /wrapper/i);
  assert.deepEqual(result.runtimeState, {});
  assert.deepEqual(result.externalRefs, {});
  // BaseController's verbs throw "abstract - subclass must override"; a no-op route that forgot to override
  // them would turn every interrupt into a rejected control instead of a harmless one.
  await assert.doesNotReject(() => controller.injectMessage({ text: "hi" }));
  await assert.doesNotReject(() => controller.interrupt({}));
  await assert.doesNotReject(() => controller.steer({ text: "more" }));
  await assert.doesNotReject(() => shape.interrupt());
  await assert.doesNotReject(() => shape.steer());
});

test("managedViaWrapper only captures MANAGED runs", () => {
  // The wrapper branch is gated on the mode as well as the flag. A resident run swallowed by the no-op would
  // resolve "delegated" while the app-server path was the thing expected to deliver it.
  for (const mode of ["resident", "channel"]) {
    assert.ok(implFor({ executionMode: mode, managedViaWrapper: true, agentInfo: LIVE_APP_SERVER })
      instanceof CodexLegacyController, `${mode} was captured by the wrapper no-op`);
  }
});

// ── managed vs legacy ───────────────────────────────────────────────────────

test("managed without the wrapper goes to the session-pool controller", () => {
  assert.ok(implFor({ executionMode: "managed" }) instanceof CodexManagedController);
  assert.ok(implFor({ executionMode: "managed", managedViaWrapper: false }) instanceof CodexManagedController);
});

test("resident and channel go to the app-server controller", () => {
  for (const mode of ["resident", "channel"]) {
    assert.ok(implFor({ executionMode: mode, agentInfo: LIVE_APP_SERVER }) instanceof CodexLegacyController,
      `${mode} did not reach the app-server controller`);
  }
});

test("an unrecognised mode falls back to the app-server controller", () => {
  // Not the managed pool: the fallback has to match what the legacy createCodexController did, or a run with
  // a mode this bridge does not know starts a codex session in the wrong shape.
  //
  // An EMPTY mode is a different case and belongs with the default below, not here: `"" ||` is falsy, so it
  // never reaches the comparison at all and resolves to "managed".
  for (const mode of ["detached", "whatever", "single-shot"]) {
    assert.ok(implFor({ executionMode: mode }) instanceof CodexLegacyController,
      `${JSON.stringify(mode)} was routed to the managed pool`);
  }
  assert.ok(implFor({ executionMode: "" }) instanceof CodexManagedController,
    "an empty mode must take the default, not the unrecognised-mode fallback");
});

test("FINDING: the app-server term in the routing cannot change the route", () => {
  // MEASURED. The dispatcher computes
  //
  //     hasWsAppServer = (resident || channel) && hasCodexLiveAppServer(cfg)
  //
  // and uses it only in `executionMode === "managed" && !hasWsAppServer`. `hasWsAppServer` requires the mode
  // to be resident or channel, which the same condition has already excluded - so the term is always true
  // there and the branch reduces to `executionMode === "managed"`. `getRuntimeConfig` and
  // `hasCodexLiveAppServer` therefore have no effect on which controller is picked.
  //
  // It is not a routing gap: the app-server decision that MATTERS is made inside CodexLegacyController's own
  // start() (codex-legacy-controller.js:92, same predicate on the same config), which is where the url is
  // either used or replaced by a freshly spawned app-server. The dispatcher's copy is a duplicate that reads
  // like a fourth route and is not one.
  //
  // NOT FIXED HERE. Removing it is a behaviour-preserving simplification, but it is the kind of edit a
  // reviewer should approve rather than one that arrives inside a test slice. Pinned so the equivalence is
  // recorded: every mode routes the same with and without a live app-server url.
  for (const mode of ["managed", "resident", "channel", "", "unknown"]) {
    const withUrl = implFor({ executionMode: mode, agentInfo: LIVE_APP_SERVER }).constructor.name;
    const without = implFor({ executionMode: mode, agentInfo: NO_APP_SERVER }).constructor.name;
    assert.equal(withUrl, without,
      `mode ${JSON.stringify(mode)} routes differently with an app-server url - the term is now LIVE, ` +
      "and this test is what should be updated to describe the new route");
  }
});

test("a missing runtimeConfig is not a crash on the routing path", () => {
  // getRuntimeConfig reads `agentInfo.runtimeConfig`, and agentInfo is absent for plenty of callers. An
  // exception here fails the dispatch before any controller exists to report why.
  assert.doesNotThrow(() => implFor({ executionMode: "resident" }));
  assert.doesNotThrow(() => implFor({}));
  assert.doesNotThrow(() => implFor({ agentInfo: {} }));
});

// ── mode resolution ─────────────────────────────────────────────────────────

test("the mode is read from opts, then the run, then the agent — in that order", () => {
  assert.ok(implFor({ run: { executionMode: "resident" }, agentInfo: LIVE_APP_SERVER })
    instanceof CodexLegacyController, "the run's mode was ignored");
  assert.ok(implFor({ agentInfo: { ...LIVE_APP_SERVER, sessionMode: "resident" } })
    instanceof CodexLegacyController, "the agent's session mode was ignored");
  // opts beats the run...
  assert.ok(implFor({ executionMode: "managed", run: { executionMode: "resident" } })
    instanceof CodexManagedController);
  // ...and the run beats the agent.
  assert.ok(implFor({
    run: { executionMode: "managed" }, agentInfo: { ...LIVE_APP_SERVER, sessionMode: "resident" },
  }) instanceof CodexManagedController);
  // With nothing said at all, managed is the default.
  assert.ok(implFor({}) instanceof CodexManagedController);
});

test("the mode is compared case- and whitespace-insensitively", () => {
  // These arrive as JSON from the server and as strings from env. " Resident " reaching the managed pool
  // would start a second codex session behind a thread the operator is already attached to.
  for (const raw of ["  RESIDENT ", "Channel", "\tresident\n"]) {
    assert.ok(implFor({ executionMode: raw, agentInfo: LIVE_APP_SERVER }) instanceof CodexLegacyController,
      `${JSON.stringify(raw)} was not recognised`);
  }
  assert.ok(implFor({ executionMode: " MANAGED " }) instanceof CodexManagedController);
});

// ── delegation ──────────────────────────────────────────────────────────────

test("each verb reaches its OWN counterpart on the chosen implementation", async () => {
  // The routing settles which impl is chosen; this settles whether the verbs forward to the matching method
  // with the caller's own arguments. An interrupt delivered as a steer cancels a turn that was meant to be
  // appended to - and a delegator is where that swap hides.
  //
  // The spy replaces the private `_impl` deliberately: the impl is chosen in the constructor, so there is no
  // seam to inject through, and the alternative is starting a real codex session.
  const calls = [];
  const sink = { marker: "codex-terminal-sink" };
  const controller = new CodexController({ executionMode: "managed", managedViaWrapper: true });
  controller._impl = {
    start: (ctx) => { calls.push(["start", ctx]); return { capabilities: {}, promise: Promise.resolve({}) }; },
    injectMessage: async (opts) => { calls.push(["injectMessage", opts]); return "injected"; },
    interrupt: async (opts) => { calls.push(["interrupt", opts]); return "interrupted"; },
    steer: async (opts) => { calls.push(["steer", opts]); return "steered"; },
    get terminalSink() { return sink; },
  };

  const injectArg = { text: "message" };
  const interruptArg = { reason: "operator" };
  const steerArg = { text: "extra" };
  const ctx = { runId: "run_1" };

  assert.equal(await controller.injectMessage(injectArg), "injected", "the impl's return value is dropped");
  assert.equal(await controller.interrupt(interruptArg), "interrupted");
  assert.equal(await controller.steer(steerArg), "steered");
  controller.start(ctx);
  assert.equal(controller.terminalSink, sink, "the terminalSink getter did not delegate");

  assert.deepEqual(calls.map(([name]) => name), ["injectMessage", "interrupt", "steer", "start"]);
  assert.equal(calls[0][1], injectArg);
  assert.equal(calls[1][1], interruptArg);
  assert.equal(calls[2][1], steerArg);
  assert.equal(calls[3][1], ctx, "start did not forward its context");
});

test("setReadyListener reaches the sub-implementation", () => {
  // markReady() becomes PATCH /agents/{id}/ready, which is how an agent stops being merely "online" and
  // becomes dispatchable. A listener parked on the outer object leaves it online forever and never ready.
  const controller = new CodexController({ executionMode: "managed", managedViaWrapper: true });
  let readyCount = 0;
  controller.setReadyListener(() => { readyCount += 1; });
  controller.start();
  assert.equal(readyCount, 1);
});

test("setReadyListener survives a sub-impl that cannot take one", () => {
  const controller = new CodexController({ executionMode: "managed", managedViaWrapper: true });
  controller._impl = { start: () => ({}) };
  assert.doesNotThrow(() => controller.setReadyListener(() => {}));
});
