// What the claude, pi and opencode controllers REFUSE, and how.
//
// Twelfth cluster off the V8-coverage census, and the rest of the controller family: `injectMessage`,
// `interrupt` and `steer` on ClaudeController (3), PiController (2) and OpencodeController (3) all had a zero
// call count. These three own no dispatcher routing - they are the leaves - so their whole observable
// contract IS which verbs they perform, which they refuse, and what the refusal says.
//
// A REFUSAL IS A FEATURE HERE. Each of these runtimes delivers somewhere this controller is not:
//
//   * claude-code: BOTH resident and managed delivery belong to the `claude-channel.js` channel-sidecar
//     inside claude-aify (claude is NOT managed-via-wrapper). This controller is a safety belt - if any path
//     still routes a claude run through launchRuntimeRun, it must fail with a message that names the surface
//     the operator should be using, not fail obscurely or, worse, appear to succeed.
//   * pi: `omp --mode rpc` is single-client stdio, delivery is `session.runTurn`, and there is no inject path
//     at all - mid-turn text goes through steer.
//   * opencode: single-shot per dispatch, so likewise no inject.
//
// NOTHING HERE LAUNCHES A RUNTIME. Every constructor is deliberately cheap (each says so), and the verbs are
// reached with no session acquired or with an injected fake handle. In particular no opencode CLI is invoked -
// the operator's opencode is wired to a local model that must not be woken by a test suite.

import assert from "node:assert/strict";
import test from "node:test";

import { ClaudeController } from "../controllers/claude-controller.js";
import { PiController } from "../controllers/pi-controller.js";
import { OpencodeController } from "../controllers/opencode-controller.js";
import { controlCapabilitiesForRuntime } from "../runtimes.js";

// ── claude: the safety belt ─────────────────────────────────────────────────

const POINTS_AT_THE_RIGHT_SURFACE = /claude-aify/;

test("claude's start() rejects, and says where the work actually goes", async () => {
  const controller = new ClaudeController({});
  const shape = controller.start();

  await assert.rejects(() => shape.promise, (err) => {
    assert.match(err.message, POINTS_AT_THE_RIGHT_SURFACE,
      "an operator reading this needs the name of the surface that WOULD work");
    assert.match(err.message, /no longer uses claude -p/);
    return true;
  });
  assert.deepEqual(shape.capabilities, controlCapabilitiesForRuntime("claude-code"),
    "capabilities must come from the claude adapter, not a literal");
});

test("claude's inject and steer refuse with the same guidance; interrupt is a silent no-op", async () => {
  const controller = new ClaudeController({});
  await assert.rejects(() => controller.injectMessage({ text: "hi" }), POINTS_AT_THE_RIGHT_SURFACE);
  await assert.rejects(() => controller.steer({ text: "more" }), POINTS_AT_THE_RIGHT_SURFACE);
  // interrupt deliberately does NOT throw: the channel-sidecar owns the live turn, so an interrupt aimed here
  // has nothing to cancel and is not an error. Throwing would surface a failed control for a no-op.
  await assert.doesNotReject(() => controller.interrupt({}));
  assert.equal(await controller.interrupt({}), undefined);
});

test("claude's start-shape steer rejects while its interrupt stays a plain no-op", async () => {
  const shape = new ClaudeController({}).start();
  await assert.rejects(async () => shape.steer(), POINTS_AT_THE_RIGHT_SURFACE);
  assert.equal(shape.interrupt(), undefined);
  await shape.promise.catch(() => {});
});

test("claude marks ready ONCE, however many times start is called", async () => {
  // markReady becomes PATCH /agents/{id}/ready. It has to fire (claude-aify is ready by virtue of being
  // launched, so operators see the same surface as other runtimes) and it has to fire once - a ready PATCH
  // per start() is avoidable chatter on the hottest path.
  const controller = new ClaudeController({});
  let readyCount = 0;
  controller.setReadyListener(() => { readyCount += 1; });

  const first = controller.start();
  const second = controller.start();
  assert.equal(readyCount, 1, "ready fired per start() call");
  assert.deepEqual(second.capabilities, first.capabilities, "the second start lost the capabilities");
  await Promise.all([first.promise.catch(() => {}), second.promise.catch(() => {})]);
});

// ── pi: no inject, and a steer that explains the right failure ──────────────

test("pi refuses an inject and points at steer", async () => {
  await assert.rejects(() => new PiController({}).injectMessage({ text: "hi" }),
    /does not support mid-session message injection; use steer\(\)/);
});

test("pi's interrupt with no live turn resolves instead of throwing", async () => {
  // Reached when a control arrives after the turn finished, or before it started. An interrupt that threw
  // there would report a failed control for a turn that is already gone.
  await assert.doesNotReject(() => new PiController({}).interrupt({}));
});

test("pi's steer with no live turn says so", async () => {
  await assert.rejects(() => new PiController({}).steer("text"), /No active Pi turn to steer/);
});

test("pi's steer reports the SESSION-ACQUIRE failure ahead of the missing turn", async () => {
  // When the pool could not give us a session, "No active Pi turn to steer" is true and useless. The acquire
  // error is the one that says why - a wiped session handle, omp missing, a resume that failed.
  const controller = new PiController({});
  controller._acquireError = new Error("omp exited before agent_ready");
  await assert.rejects(() => controller.steer("text"), /omp exited before agent_ready/);
});

test("pi's interrupt and steer forward to the live turn handle", async () => {
  // The turn handle comes from `session.runTurn` inside start(), which would acquire a real omp process; the
  // handle is injected instead so the forwarding is what gets tested rather than the pool.
  const calls = [];
  const controller = new PiController({});
  controller._turnHandle = {
    interrupt: async () => { calls.push(["interrupt"]); },
    steer: async (text) => { calls.push(["steer", text]); },
  };

  await controller.interrupt({});
  await controller.steer("append this");
  assert.deepEqual(calls, [["interrupt"], ["steer", "append this"]]);
});

// ── opencode: single-shot, and an interrupt that must be remembered ─────────

test("opencode refuses an inject", async () => {
  await assert.rejects(() => new OpencodeController({}).injectMessage({ text: "hi" }),
    /does not support mid-session message injection/);
});

test("opencode's steer with no session says so", async () => {
  await assert.rejects(() => new OpencodeController({}).steer("text"),
    /No active OpenCode session to steer/);
});

test("an opencode interrupt that arrives BEFORE the session is remembered, not dropped", async () => {
  // The ordering is the contract: `_interrupted = true` is set before the early return. An interrupt racing
  // session creation is the normal case for a fast Stop, and forgetting it lets the turn run to completion
  // after the operator cancelled it.
  const controller = new OpencodeController({});
  await controller.interrupt({});
  assert.equal(controller._interrupted, true, "the interrupt was dropped because no session existed yet");
});

test("opencode's interrupt aborts the live session by id and directory", async () => {
  // Both fields matter: the opencode server keys sessions by id WITHIN a directory, so an abort missing the
  // directory can miss the session it was aimed at.
  const aborts = [];
  const controller = new OpencodeController({});
  controller._open = { client: { session: { abort: async (args) => { aborts.push(args); } } } };
  controller._sessionId = "ses_123";
  controller._cwd = "C:/work/project";

  await controller.interrupt({});
  assert.deepEqual(aborts, [{ path: { id: "ses_123" }, query: { directory: "C:/work/project" } }]);
  assert.equal(controller._interrupted, true);
});

test("opencode's steer sends the text as a prompt part to the live session", async () => {
  const prompts = [];
  const controller = new OpencodeController({});
  controller._open = {
    client: { session: { promptAsync: async (args) => { prompts.push(args); return {}; } } },
  };
  controller._sessionId = "ses_123";
  controller._cwd = "C:/work/project";

  await controller.steer("more context");
  assert.deepEqual(prompts, [{
    path: { id: "ses_123" },
    query: { directory: "C:/work/project" },
    body: { parts: [{ type: "text", text: "more context" }] },
  }]);
});

test("opencode's steer coerces a missing text to an empty part rather than the string \"undefined\"", async () => {
  const prompts = [];
  const controller = new OpencodeController({});
  controller._open = {
    client: { session: { promptAsync: async (args) => { prompts.push(args); return {}; } } },
  };
  controller._sessionId = "ses_1";
  await controller.steer(undefined);
  assert.equal(prompts[0].body.parts[0].text, "", "the agent would have been sent the word \"undefined\"");
});

test("opencode's steer surfaces the server's error message, not a generic one", async () => {
  // `promptAsync` resolves with an error payload rather than throwing, so a steer that failed would look like
  // one that worked if the result were not inspected.
  const controller = new OpencodeController({});
  controller._sessionId = "ses_1";
  controller._open = {
    client: {
      session: {
        promptAsync: async () => ({ error: { data: { message: "session is busy" } } }),
      },
    },
  };
  await assert.rejects(() => controller.steer("x"), /session is busy/);
});

test("opencode's steer falls back through the error shapes it might be handed", async () => {
  const controller = new OpencodeController({});
  controller._sessionId = "ses_1";
  const withError = (error) => {
    controller._open = { client: { session: { promptAsync: async () => ({ error }) } } };
    return controller;
  };
  await assert.rejects(() => withError({ message: "outer message" }).steer("x"), /outer message/);
  await assert.rejects(() => withError({}).steer("x"), /OpenCode steer failed/);
});
