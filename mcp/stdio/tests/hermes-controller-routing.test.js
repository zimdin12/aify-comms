// HermesController: which implementation a hermes dispatch is routed to, and whether the four delivery verbs
// reach it.
//
// Tenth cluster off the V8-coverage census. Every runtime controller's delivery verbs came back never-called
// (34 functions across eight files); this is the first of them, and hermes is the one with the history.
// `injectMessage`, `interrupt`, `steer`, `setReadyListener` and the `terminalSink` getter had a zero count.
//
// WHAT THE ROUTING DECIDES. This controller picks its implementation ONCE, at construction, from the
// execution mode - and the three destinations behave nothing alike:
//
//   * managed + managedViaWrapper -> a no-op. The wrapper's own child bridge claims and delivers, so this
//     controller must stay out of the way, and it declares interrupt/steer false to say it performs neither.
//     (On the declared capabilities' reach, see the FINDING below - they are narrower than they look.)
//   * channel / resident -> also a no-op, for a different reason. Delivery belongs to the per-agent
//     `hermes-managed-host.js run <agent>` loop that submits into the visible TUI. The path is documented as
//     one that must stay dead: when this controller resolved "delegated" and server.js auto-mirrored the
//     summary, the operator got FABRICATED replies instead of the agent's own.
//   * anything else -> HermesManagedController, the one that actually delivers.
//
// A mis-route is therefore not a wrong-looking log line. It is either a dead dispatch or an invented reply.
//
import assert from "node:assert/strict";
import test from "node:test";

import { HermesController } from "../controllers/hermes-controller.js";
import { HermesManagedController } from "../controllers/hermes-managed-controller.js";
import { controlCapabilitiesForRuntime } from "../runtimes.js";

// start() returns the legacy shape: { capabilities, interrupt, steer, promise }.
async function startAndResolve(controller) {
  const shape = controller.start();
  return { shape, result: await shape.promise };
}

// ── the wrapper-delegated route ──────────────────────────────────────────────

test("managed + managedViaWrapper resolves as delegated and claims NO control capabilities", async () => {
  const controller = new HermesController({ executionMode: "managed", managedViaWrapper: true });
  const { shape, result } = await startAndResolve(controller);

  assert.deepEqual(shape.capabilities, { interrupt: false, steer: false },
    "advertising a control this route cannot perform puts a dead button on the dashboard");
  assert.equal(result.status, "delegated");
  assert.match(result.summary, /wrapper/i, "the summary must say WHO owns this delivery");
  assert.deepEqual(result.runtimeState, {});
  assert.deepEqual(result.externalRefs, {});
});

test("the wrapper-delegated route's verbs resolve instead of throwing", async () => {
  // BaseController's verbs throw "abstract - subclass must override". A no-op route that forgot to override
  // them would turn every interrupt into a rejected control rather than a silent, harmless one.
  const controller = new HermesController({ executionMode: "managed", managedViaWrapper: true });
  await assert.doesNotReject(() => controller.injectMessage({ text: "hi" }));
  await assert.doesNotReject(() => controller.interrupt({}));
  await assert.doesNotReject(() => controller.steer({ text: "more" }));
  const shape = controller.start();
  await assert.doesNotReject(() => shape.interrupt());
  await assert.doesNotReject(() => shape.steer());
});

// ── the channel/resident dead route ──────────────────────────────────────────

for (const mode of ["channel", "resident"]) {
  test(`${mode} is routed to the dead no-op, not to the delivering controller`, async () => {
    const controller = new HermesController({ executionMode: mode });
    const { shape, result } = await startAndResolve(controller);

    assert.deepEqual(shape.capabilities, { interrupt: false, steer: false });
    assert.equal(result.status, "delegated");
    assert.match(result.summary, /delivery loop/i,
      "the summary must point at the hermes-managed-host loop that actually delivers");
    assert.doesNotMatch(result.summary, /wrapper/i, `${mode} was routed to the WRAPPER no-op`);
  });
}

test("managedViaWrapper does NOT capture a channel run", async () => {
  // The wrapper branch is gated on managed mode. If it also swallowed channel runs, a channel dispatch would
  // be answered by the wrapper's no-op while the delivery loop was the thing waiting to claim it.
  const controller = new HermesController({ executionMode: "channel", managedViaWrapper: true });
  const { result } = await startAndResolve(controller);
  assert.match(result.summary, /delivery loop/i);
});

// ── mode resolution ─────────────────────────────────────────────────────────

test("the mode is read from opts, then the run, then the agent — in that order", async () => {
  const summaryOf = async (opts) => (await startAndResolve(new HermesController(opts))).result.summary;

  // run.executionMode is consulted when opts carries none.
  assert.match(await summaryOf({ run: { executionMode: "channel" } }), /delivery loop/i);
  // agentInfo.sessionMode is the last resort before the default.
  assert.match(await summaryOf({ agentInfo: { sessionMode: "channel" } }), /delivery loop/i);
  // An explicit opts mode WINS over the run's.
  const explicit = new HermesController({
    executionMode: "managed", managedViaWrapper: true, run: { executionMode: "channel" },
  });
  assert.match((await startAndResolve(explicit)).result.summary, /wrapper/i);
  // And the run's mode wins over the agent's.
  assert.match(await summaryOf({
    run: { executionMode: "channel" }, agentInfo: { sessionMode: "managed" },
  }), /delivery loop/i);
});

test("the mode is compared case- and whitespace-insensitively", async () => {
  // These values arrive from the server as JSON and from env as strings. A mode of " Channel " routing to the
  // DELIVERING controller would fork a hidden hermes session behind the operator's visible TUI.
  for (const raw of ["  CHANNEL ", "Resident", "\tchannel\n"]) {
    const { result } = await startAndResolve(new HermesController({ executionMode: raw }));
    assert.match(result.summary, /delivery loop/i, `${JSON.stringify(raw)} escaped the dead route`);
  }
});

test("no mode at all falls through to the DELIVERING controller", () => {
  // The default has to be the one that delivers: defaulting to a no-op would strand every dispatch that
  // omitted a mode, and it would look like a healthy "delegated" resolution rather than a failure.
  // Asserted without start(), which would spawn a real hermes session.
  {
    for (const opts of [{}, { executionMode: "" }, { executionMode: "managed" }, { run: {}, agentInfo: {} }]) {
      assert.ok(new HermesController(opts)._impl instanceof HermesManagedController,
        `${JSON.stringify(opts)} was routed away from the delivering controller`);
    }
  }
});

test("FINDING: the ACP sub-mode DECLARES a steer it rejects, in a field nothing reads", async () => {
  // MEASURED. `adapters/hermes.js` declares `supportsSteering => true` unconditionally, so the controller's
  // capabilities say `steer: true` while its own `steer()` throws "Hermes ACP fallback does not support mid-turn
  // steer". The `capabilities` key of the legacy start() shape has NO reader: `dispatch-loop.mjs` consumes
  // `.promise` and nothing else, and the capabilities the control plane acts on come from
  // `defaultCapabilitiesForRuntime` at REGISTRATION, plus the service-side strip of `steer` from managed hermes
  // (af9e937). So this costs nothing today; it is a field that reads as authoritative and is not.
  //
  // NOT FIXED HERE: narrowing a declared capability touches steer-vs-queue routing. Both halves are pinned so
  // the day either side moves, it moves visibly. (The gateway sub-mode it was once compared with went in 0.9.10.)
  const acp = new HermesController({})._impl._capabilities;
  assert.deepEqual(acp, controlCapabilitiesForRuntime("hermes"),
    "managed capabilities must come from the hermes ADAPTER, not a literal in the controller");
  assert.equal(acp.steer, true, "the ACP sub-mode still advertises steer");

  // And the other half: the advertised verb rejects. No session is needed to reach it.
  await assert.rejects(() => new HermesController({})._impl.steer("more text"), /does not support mid-turn steer/);
});

// ── delegation ──────────────────────────────────────────────────────────────

test("each verb reaches its OWN counterpart on the chosen implementation", async () => {
  // The routing above settles WHICH impl is chosen; what remains is whether the four verbs forward to the
  // matching method with the caller's own arguments. A steer that arrives as an interrupt is the exact shape
  // of the 2026-07-20 hermes defect, and a delegator is where that swap hides.
  //
  // The spy replaces the private `_impl` deliberately: the impl is chosen in the constructor, so there is no
  // seam to inject through, and the alternative is starting a real hermes session.
  const calls = [];
  const sink = { marker: "terminal-sink" };
  const controller = new HermesController({ executionMode: "managed", managedViaWrapper: true });
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
  assert.equal(calls[0][1], injectArg, "injectMessage was handed a different object");
  assert.equal(calls[1][1], interruptArg, "interrupt was handed a different object");
  assert.equal(calls[2][1], steerArg, "steer was handed a different object");
  assert.equal(calls[3][1], ctx, "start did not forward its context");
});

test("setReadyListener reaches the sub-implementation, not just the wrapper object", async () => {
  // The bridge turns markReady() into PATCH /agents/{id}/ready, which is how an agent stops being merely
  // "online" and becomes dispatchable. A listener parked on the outer controller while the sub-impl calls
  // markReady() leaves the agent online forever and never ready.
  const controller = new HermesController({ executionMode: "managed", managedViaWrapper: true });
  let readyCount = 0;
  controller.setReadyListener(() => { readyCount += 1; });
  controller.start();
  assert.equal(readyCount, 1, "ready never reached the listener the bridge registered");
});

test("setReadyListener survives a sub-impl that has no such method", () => {
  // Defensive: the outer controller forwards only when the impl can receive it. Assuming the method exists
  // would turn one controller shape into a TypeError on the start path.
  const controller = new HermesController({ executionMode: "managed", managedViaWrapper: true });
  controller._impl = { start: () => ({}) };
  assert.doesNotThrow(() => controller.setReadyListener(() => {}));
});
