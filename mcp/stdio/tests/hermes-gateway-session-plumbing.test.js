// What the managed-hermes gateway session does WITHOUT a gateway: its console feed, its pool events, and its
// cancel.
//
// Twenty-fourth cluster off the V8-coverage census, which lists 13 never-called functions in
// `hermes-managed-gateway-session.js` — the largest single-file cluster left. This slice takes the ones that
// need no hermes process: `attachTerminalSink`, `detachTerminalSink`, `_emit` and `cancelActiveTurn`. The socket
// plumbing (`_openSocket`, `_sendRpc`, `_onSocketMessage`, `waitForReady`, `fetchToken`, `pickPort`) is reachable
// only through `ensureStarted()`, which spawns `hermes dashboard`; that needs its own harness and is its own
// slice.
//
// THE CONSOLE FEED IS A HARD REQUIREMENT. A managed agent must show its real TUI in the dashboard, and this sink
// is how the gateway session's output gets there. Two properties are easy to lose and invisible when they break:
// frames must arrive IN ORDER (the sink is async and awaited through a chain), and a sink that FAILS must not
// take the session with it — the console is a view, and losing it must never end a turn.
//
// The constructor only assigns fields, so the session is constructed directly and `_sendRpc` is replaced on the
// instance. Nothing spawns, nothing connects.
//
// THREE MUTATIONS SURVIVE ALONE AND ARE CAUGHT IN PAIRS, which is the honest description of this code: the type
// guards on `attachTerminalSink` and on the constructor's `onPoolEvent`, and the `if (!this._terminalSink) return`
// early exit, each sit behind a `catch {}` that would swallow the resulting TypeError. Removing any one changes
// nothing observable; removing a guard TOGETHER with the catch that shadows it fails these tests. So the
// properties are covered — the guards are simply doubled, and the mutation harness carries the paired versions
// so that stays provable rather than asserted in prose.

import assert from "node:assert/strict";
import test from "node:test";

import { HermesManagedGatewaySession } from "../hermes-managed-gateway-session.js";

const session = (opts = {}) => new HermesManagedGatewaySession({ agentId: "gw-session-agent", ...opts });

// The flush chain is a promise chain, so give it a turn to drain.
const drain = () => new Promise((resolve) => setTimeout(resolve, 20));

// ── the terminal sink ───────────────────────────────────────────────────────

test("an attached sink receives the frame's body and status", async () => {
  const frames = [];
  const s = session();
  s.attachTerminalSink((body, status) => { frames.push([body, status]); });

  s._pushTerminalFrame("hello from hermes", "running");
  await drain();
  assert.deepEqual(frames, [["hello from hermes", "running"]]);
});

test("frames are delivered IN ORDER through a slow sink", async () => {
  // The sink POSTs to the service, so it is async and its calls can overlap. Terminal output that arrives out of
  // order is worse than none: the operator reads a console that never happened.
  const order = [];
  const s = session();
  s.attachTerminalSink(async (body) => {
    // Deliberately inverted delays: the first frame is the slowest, so anything but a serialising chain reorders.
    await new Promise((resolve) => setTimeout(resolve, body === "first" ? 40 : 1));
    order.push(body);
  });

  s._pushTerminalFrame("first");
  s._pushTerminalFrame("second");
  s._pushTerminalFrame("third");
  await new Promise((resolve) => setTimeout(resolve, 200));

  assert.deepEqual(order, ["first", "second", "third"], "the frames were not serialised");
});

test("a sink that REJECTS does not stop the frames after it", async () => {
  // The console is a view of the turn, never the turn itself. A service hiccup on one frame must not silence the
  // feed or surface as a session error.
  const seen = [];
  const s = session();
  s.attachTerminalSink(async (body) => {
    if (body === "boom") throw new Error("sink failed");
    seen.push(body);
  });

  s._pushTerminalFrame("before");
  s._pushTerminalFrame("boom");
  s._pushTerminalFrame("after");
  await new Promise((resolve) => setTimeout(resolve, 120));

  assert.deepEqual(seen, ["before", "after"], "a failing frame took the rest of the feed with it");
});

test("a sink that throws SYNCHRONOUSLY is also contained", async () => {
  const seen = [];
  const s = session();
  s.attachTerminalSink((body) => {
    if (body === "boom") throw new Error("sink failed");
    seen.push(body);
  });

  s._pushTerminalFrame("before");
  s._pushTerminalFrame("boom");
  s._pushTerminalFrame("after");
  await new Promise((resolve) => setTimeout(resolve, 120));
  assert.deepEqual(seen, ["before", "after"]);
});

test("attaching a NON-function clears the sink instead of installing it", async () => {
  // The guard matters because `_pushTerminalFrame` calls whatever is stored. A stored string would throw on the
  // first frame of every turn — inside a promise chain with no caller.
  for (const notAFunction of [null, undefined, "sink", 42, {}]) {
    const s = session();
    s.attachTerminalSink((body) => { throw new Error(`the old sink ran for ${body}`); });
    s.attachTerminalSink(notAFunction);
    s._pushTerminalFrame("after the clear");
    await drain();
  }
  assert.ok(true, "no frame reached a cleared sink and nothing threw");
});

test("detaching stops the feed", async () => {
  const frames = [];
  const s = session();
  s.attachTerminalSink((body) => { frames.push(body); });
  s._pushTerminalFrame("while attached");
  await drain();
  s.detachTerminalSink();
  s._pushTerminalFrame("after detach");
  await drain();
  assert.deepEqual(frames, ["while attached"]);
});

test("a frame with neither body nor status is not sent at all", async () => {
  // The runtime emits empty deltas. Forwarding them would be one HTTP POST per no-op, per agent, per turn.
  let calls = 0;
  const s = session();
  s.attachTerminalSink(() => { calls += 1; });

  s._pushTerminalFrame("", "");
  s._pushTerminalFrame(null);
  s._pushTerminalFrame(undefined, undefined);
  await drain();
  assert.equal(calls, 0, "an empty frame was forwarded");

  // …but a STATUS-only frame is real: it is how the console shows a state change with no output.
  s._pushTerminalFrame("", "starting");
  await drain();
  assert.equal(calls, 1, "a status-only frame was dropped");
});

test("frames pushed with no sink attached are dropped, and the feed still works afterwards", async () => {
  // Two properties in one, because they fail together. Frames from before a sink existed must not be replayed
  // (attaching a console mid-turn would otherwise dump the whole turn as if it were happening now), AND the
  // flush chain must survive them — a chain left in a rejected state delivers nothing ever again, so the console
  // would be permanently blank for an agent whose turn is running fine.
  const frames = [];
  const s = session();
  s._pushTerminalFrame("before any sink");
  await drain();
  s.attachTerminalSink((body) => { frames.push(body); });
  await drain();
  assert.deepEqual(frames, [], "output from before the sink existed was replayed into it");

  s._pushTerminalFrame("after the sink exists");
  await drain();
  assert.deepEqual(frames, ["after the sink exists"],
    "the flush chain never recovered — an early frame poisoned the whole feed");
});

// ── pool events ─────────────────────────────────────────────────────────────

test("pool events reach the listener with their kind and payload", async () => {
  const events = [];
  const s = session({ onPoolEvent: (kind, payload) => events.push([kind, payload]) });
  s._emit("spawn", { port: 8926 });
  assert.deepEqual(events, [["spawn", { port: 8926 }]]);
});

test("a session with NO pool listener emits harmlessly", async () => {
  // `onPoolEvent` is optional, and the emit sites are on the spawn/ready paths — a missing listener must not be
  // an exception thrown from inside a startup sequence.
  assert.doesNotThrow(() => session()._emit("spawn", { port: 1 }));
  // A non-function is normalised to none at construction, for the same reason.
  assert.doesNotThrow(() => session({ onPoolEvent: "not a function" })._emit("spawn", { port: 1 }));
});

test("a THROWING pool listener does not break the emitter", async () => {
  // The listener is a controller callback that logs; it is not allowed to fail a spawn.
  const s = session({ onPoolEvent: () => { throw new Error("listener blew up"); } });
  assert.doesNotThrow(() => s._emit("spawn", { port: 1 }));
});

// ── cancelActiveTurn ────────────────────────────────────────────────────────

test("cancelling with no active turn sends nothing", async () => {
  const sent = [];
  const s = session();
  s._sendRpc = async (frame) => { sent.push(frame); };
  s._sessionId = "sess-1";

  await s.cancelActiveTurn();
  assert.deepEqual(sent, [], "an interrupt was sent for a turn that does not exist");
});

test("cancelling with no session id sends nothing", async () => {
  const sent = [];
  const s = session();
  s._sendRpc = async (frame) => { sent.push(frame); };
  s._activeTurn = { settled: false };

  await s.cancelActiveTurn();
  assert.deepEqual(sent, []);
  assert.equal(s._activeTurn.settled, false, "the turn was marked settled without anything being cancelled");
});

test("cancelling a live turn interrupts THAT session and settles the turn", async () => {
  const sent = [];
  const s = session();
  s._sendRpc = async (frame) => { sent.push(frame); };
  s._sessionId = "sess-42";
  s._activeTurn = { settled: false };

  await s.cancelActiveTurn();
  assert.equal(sent.length, 1);
  assert.equal(sent[0].method, "session.interrupt");
  assert.deepEqual(sent[0].params, { session_id: "sess-42" },
    "the interrupt did not name the session it was meant to stop");
  assert.equal(s._activeTurn.settled, true, "the turn was left unsettled after a successful interrupt");
});

test("a FAILED interrupt still settles the turn", async () => {
  // The turn is waiting for a completion event that will never arrive now. Leaving it unsettled hangs the
  // dispatch until a backstop expires, and the operator's Stop looks like it did nothing.
  const s = session();
  s._sendRpc = async () => { throw new Error("socket gone"); };
  s._sessionId = "sess-42";
  s._activeTurn = { settled: false };

  await assert.doesNotReject(() => s.cancelActiveTurn());
  assert.equal(s._activeTurn.settled, true, "a failed interrupt left the turn hanging");
});
