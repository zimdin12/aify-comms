// The two MANAGED leaf controllers — hermes and codex — and the verbs their sessions actually perform.
//
// Thirteenth cluster off the V8-coverage census: `_legacyShape`, `injectMessage`, `interrupt`, `steer` and
// `start` on HermesManagedController and CodexManagedController, five each, all with a zero call count. These
// are the controllers a managed dispatch really lands on once the routers above them have chosen (those
// routers are covered by hermes-controller-routing / codex-controller-routing).
//
// THEY ARE NEAR-MIRRORS, and that is the reason to test them together: the same file shape, the same session
// pool pattern, the same refusal wording — and two deliberate divergences that a copy-paste would erase.
// Codex's steer needs only a live session; hermes's needs a live session AND the gateway sub-mode, because
// the ACP transport has no mid-turn append at all.
//
// NO SESSION POOL IS TOUCHED. `start()` would acquire a real `hermes acp` / codex app-server session, so the
// verbs are reached with `_session` unset or with a fake, and the start-idempotency case sets the two fields
// start() would have set. Every constructor here is documented as cheap, which is what makes that honest.
//
// ONE MUTATION SURVIVES, IDENTICALLY IN BOTH FILES: deleting the `if (this._session)` guard from `interrupt`.
// The body is `try { if (this._session) await this._session.cancelActiveTurn(); } catch {}`, so without the
// guard the missing session raises a TypeError that the SAME catch swallows — the observable result is the
// resolved promise either way. The guard is a statement of intent absorbed by its own error handling, not a
// second condition, and no assertion through the public surface can separate them.

import assert from "node:assert/strict";
import test from "node:test";

import { HermesManagedController } from "../controllers/hermes-managed-controller.js";
import { CodexManagedController } from "../controllers/codex-managed-controller.js";

const BOTH = [
  ["hermes", () => new HermesManagedController({}), /hermes managed does not support direct message injection/],
  ["codex", () => new CodexManagedController({}), /codex managed does not support direct message injection/],
];

// ── the shared refusal ──────────────────────────────────────────────────────

for (const [name, make, injectMessagePattern] of BOTH) {
  test(`${name} managed refuses an inject and names the alternative`, async () => {
    // Neither runtime has an inject path: a managed turn is a whole dispatch. "Send a follow-up dispatch" is
    // the actionable half — without it the caller cannot tell a missing feature from a broken one.
    await assert.rejects(() => make().injectMessage({ text: "hi" }), injectMessagePattern);
    await assert.rejects(() => make().injectMessage({ text: "hi" }), /send a follow-up dispatch/);
  });

  test(`${name} managed interrupt with no session resolves rather than failing`, async () => {
    // Reached whenever a Stop lands before the session is up or after the turn ended.
    await assert.doesNotReject(() => make().interrupt({}));
  });

  test(`${name} managed interrupt cancels the live turn`, async () => {
    const calls = [];
    const controller = make();
    controller._session = { cancelActiveTurn: async () => { calls.push("cancelActiveTurn"); } };
    await controller.interrupt({});
    assert.deepEqual(calls, ["cancelActiveTurn"]);
  });

  test(`${name} managed interrupt SWALLOWS a cancel that throws`, async () => {
    // Deliberate. A session whose cancel fails is usually one whose turn is already gone — the operator asked
    // for the turn to stop and it has. Rejecting here reports a failed Stop for a turn that is not running,
    // and the dashboard's Stop control is the thing that would look broken.
    const controller = make();
    controller._session = { cancelActiveTurn: async () => { throw new Error("session already closed"); } };
    await assert.doesNotReject(() => controller.interrupt({}));
  });

  test(`${name} managed start() is idempotent — a second call starts no second turn`, async () => {
    // The legacy shape is what a second start must return: same promise, same capabilities. Beginning a second
    // turn for one run is a double-delivery, and it is exactly what a re-entrant claim would cause.
    // `_started`/`_promise` are set here because start() itself would acquire a real session.
    const controller = make();
    const sentinel = Promise.resolve({ status: "completed" });
    controller._started = true;
    controller._promise = sentinel;
    controller._capabilities = { interrupt: true, steer: false };

    const first = controller.start();
    const second = controller.start();
    assert.equal(first.promise, sentinel, "start() did not hand back the turn already in flight");
    assert.equal(second.promise, sentinel);
    assert.deepEqual(second.capabilities, { interrupt: true, steer: false });
    await sentinel;
  });

  test(`${name} managed legacy shape routes its controls to the instance verbs`, async () => {
    // The shape's `interrupt`/`steer` are thin wrappers, and the bridge calls THOSE, not the methods. A wrapper
    // that closed over the wrong verb would be invisible to any test that only called the methods directly.
    const calls = [];
    const controller = make();
    controller._started = true;
    controller._promise = Promise.resolve({});
    controller._session = {
      cancelActiveTurn: async () => { calls.push(["cancel"]); },
      steer: async (text) => { calls.push(["steer", text]); },
    };
    controller._useGateway = true; // hermes gates steer on this; codex ignores it

    const shape = controller.start();
    await shape.interrupt();
    await shape.steer("appended text");
    assert.deepEqual(calls, [["cancel"], ["steer", "appended text"]]);
    await controller._promise;
  });
}

// ── the divergence ──────────────────────────────────────────────────────────

test("codex managed steer needs only a live session", async () => {
  const controller = new CodexManagedController({});
  await assert.rejects(() => controller.steer("text"), /No active Codex session to steer/);

  const calls = [];
  controller._session = { steer: async (text) => { calls.push(text); } };
  await controller.steer("mid-turn text");
  assert.deepEqual(calls, ["mid-turn text"]);
});

test("hermes managed steer needs the GATEWAY sub-mode as well as a session", async () => {
  // The divergence that matters. A live ACP session is still unsteerable — the transport has no mid-turn
  // append — so a session-only check would send a steer into a path that cannot perform it. See also
  // hermes-controller-routing.test.js, which pins the capability side of this same split.
  const controller = new HermesManagedController({});
  controller._session = { steer: async () => { throw new Error("must not be reached"); } };
  controller._useGateway = false;
  await assert.rejects(() => controller.steer("text"), /ACP fallback does not support mid-turn steer/);

  const calls = [];
  controller._useGateway = true;
  controller._session = { steer: async (text) => { calls.push(text); } };
  await controller.steer("mid-turn text");
  assert.deepEqual(calls, ["mid-turn text"]);
});

test("hermes managed steer refuses the gateway sub-mode with no session, and says which failure it is", async () => {
  // Same message for both halves of the guard. Worth pinning as the CURRENT behaviour: a gateway-mode agent
  // whose session has not come up reads "ACP fallback does not support mid-turn steer", which names the wrong
  // cause. Harmless today (both are refusals) but it is what an operator would be reading.
  const controller = new HermesManagedController({});
  controller._useGateway = true;
  controller._session = null;
  await assert.rejects(() => controller.steer("text"), /ACP fallback does not support mid-turn steer/);
});
