import assert from "node:assert/strict";
import { test } from "node:test";
import {
  makeGatewayTurnDetector,
  startHermesGatewayTurnDetector,
} from "../hermes-gateway-turn-detector.js";

// ---------------------------------------------------------------------------
// makeGatewayTurnDetector — the pure, debounced, bidirectional state machine.
//
// Mirrors makeTurnEndDetector (claude) but keys on the GATEWAY's session status
// ("working" | "idle" | "" / unknown) and debounces the idle→end transition by
// N CONSECUTIVE idle observations so a momentary mid-turn idle (between tool
// calls / during a generation gap) never false-clears (the flap fix).
//
// Directives:
//   "start" — gateway RUNNING ("working") and we were not already in-flight
//   "end"   — gateway IDLE sustained for >= idleDebounce ticks (after working)
//   null    — steady state, or unknown/transient (never flips state)
// ANTI-FEEDBACK-LOOP: input is the gateway's process truth, never derived status.
// ---------------------------------------------------------------------------

test("makeGatewayTurnDetector: 'working' fires 'start' once, then steady-state null", () => {
  const d = makeGatewayTurnDetector({ idleDebounce: 3 });
  assert.equal(d.observe("working"), "start", "first working → start");
  assert.equal(d.observe("working"), null, "still working → no re-fire (edge-triggered)");
  assert.equal(d.observe("working"), null);
});

test("makeGatewayTurnDetector: momentary single idle mid-turn does NOT end (no flap)", () => {
  const d = makeGatewayTurnDetector({ idleDebounce: 3 });
  assert.equal(d.observe("working"), "start");
  // A single mid-turn idle blip (running=False between tool calls), then back to working.
  assert.equal(d.observe("idle"), null, "one idle is below the debounce → no end");
  assert.equal(d.observe("working"), null, "back to working resets the idle streak; still in-flight");
  // Another lone idle, again below threshold.
  assert.equal(d.observe("idle"), null);
  assert.equal(d.observe("idle"), null, "two consecutive idle still below 3 → no end");
  assert.equal(d.observe("working"), null, "working again resets the streak");
});

test("makeGatewayTurnDetector: N consecutive idle → ends exactly once", () => {
  const d = makeGatewayTurnDetector({ idleDebounce: 3 });
  assert.equal(d.observe("working"), "start");
  assert.equal(d.observe("idle"), null, "idle #1");
  assert.equal(d.observe("idle"), null, "idle #2");
  assert.equal(d.observe("idle"), "end", "idle #3 → turn-end latched");
  assert.equal(d.observe("idle"), null, "stays ended; no repeat fire");
  assert.equal(d.observe("idle"), null);
});

test("makeGatewayTurnDetector: a new turn after end re-arms 'start'", () => {
  const d = makeGatewayTurnDetector({ idleDebounce: 2 });
  assert.equal(d.observe("working"), "start");
  assert.equal(d.observe("idle"), null);
  assert.equal(d.observe("idle"), "end");
  // New turn begins.
  assert.equal(d.observe("working"), "start", "fresh working after end → start again");
  assert.equal(d.observe("idle"), null);
  assert.equal(d.observe("idle"), "end", "second turn ends after debounce");
});

test("makeGatewayTurnDetector: submit-race — idle BEFORE any working never ends the turn", () => {
  const d = makeGatewayTurnDetector({ idleDebounce: 2 });
  // The gateway hasn't flipped running=True yet; many idles before the first working.
  assert.equal(d.observe("idle"), null);
  assert.equal(d.observe("idle"), null);
  assert.equal(d.observe("idle"), null, "idle before ever working must NOT emit 'end'");
  assert.equal(d.observe("idle"), null);
  // Turn actually starts now.
  assert.equal(d.observe("working"), "start");
});

test("makeGatewayTurnDetector: unknown/empty status is transient — never flips state, never resets streak", () => {
  const d = makeGatewayTurnDetector({ idleDebounce: 3 });
  assert.equal(d.observe("working"), "start");
  assert.equal(d.observe("idle"), null, "idle #1");
  assert.equal(d.observe(""), null, "unknown tick: no fire, does not reset idle streak");
  assert.equal(d.observe("idle"), null, "idle #2 (unknown didn't reset)");
  assert.equal(d.observe("idle"), "end", "idle #3 → end (unknown was a no-op, not a reset)");
});

test("makeGatewayTurnDetector: 'starting'/'waiting' are transitional — not idle, not working", () => {
  const d = makeGatewayTurnDetector({ idleDebounce: 2 });
  assert.equal(d.observe("starting"), null, "starting is not 'working' → no start");
  assert.equal(d.observe("working"), "start");
  assert.equal(d.observe("waiting"), null, "waiting (pending approval) is not idle → no end-progress");
  assert.equal(d.observe("idle"), null, "idle #1");
  assert.equal(d.observe("idle"), "end", "idle #2 → end");
});

test("makeGatewayTurnDetector: default idleDebounce >= 3 (flap-safe)", () => {
  const d = makeGatewayTurnDetector();
  d.observe("working");
  assert.equal(d.observe("idle"), null);
  assert.equal(d.observe("idle"), null, "two idles must not end at the default debounce (>=3)");
});

// ---------------------------------------------------------------------------
// startHermesGatewayTurnDetector — the periodic loop (mirrors
// startClaudeTurnEndDetector). Continuous + per-agent: covers autonomous /
// direct-typed turns (no dispatch) AND removes the flap.
// ---------------------------------------------------------------------------

test("startHermesGatewayTurnDetector: gateway working with turn_busy unset → POSTs /turn-start (edge-triggered, no spam)", async () => {
  let starts = 0;
  let ends = 0;
  const statuses = ["working", "working", "working"];
  let i = 0;
  const stop = startHermesGatewayTurnDetector({
    intervalMs: 5,
    idleDebounce: 2,
    readGatewayStatus: async () => statuses[Math.min(i++, statuses.length - 1)],
    postTurnStart: async () => { starts++; },
    postTurnEnd: async () => { ends++; },
  });
  await new Promise((r) => setTimeout(r, 40));
  stop();
  assert.equal(starts, 1, "edge-triggered: /turn-start fired exactly once for a sustained working run (no per-tick spam)");
  assert.equal(ends, 0, "no /turn-end while working");
});

test("startHermesGatewayTurnDetector: sustained idle after working → POSTs /turn-end once", async () => {
  let starts = 0;
  let ends = 0;
  const statuses = ["working", "idle", "idle", "idle", "idle", "idle"];
  let i = 0;
  const stop = startHermesGatewayTurnDetector({
    intervalMs: 5,
    idleDebounce: 2,
    readGatewayStatus: async () => statuses[Math.min(i++, statuses.length - 1)],
    postTurnStart: async () => { starts++; },
    postTurnEnd: async () => { ends++; },
  });
  await new Promise((r) => setTimeout(r, 80));
  stop();
  assert.equal(starts, 1, "one start");
  assert.equal(ends, 1, "sustained idle → exactly one /turn-end (no flap, no repeat)");
});

test("startHermesGatewayTurnDetector: a gateway read error never flips state (no false turn-end)", async () => {
  let ends = 0;
  let i = 0;
  // working, then a read error, then back to working — must not end.
  const stop = startHermesGatewayTurnDetector({
    intervalMs: 5,
    idleDebounce: 2,
    readGatewayStatus: async () => {
      i++;
      if (i === 1) return "working";
      if (i <= 3) throw new Error("gateway hiccup");
      return "working";
    },
    postTurnStart: async () => {},
    postTurnEnd: async () => { ends++; },
  });
  await new Promise((r) => setTimeout(r, 50));
  stop();
  assert.equal(ends, 0, "a gateway read error is unknown → never a false /turn-end");
});

test("startHermesGatewayTurnDetector: no-op with invalid params", () => {
  assert.equal(typeof startHermesGatewayTurnDetector({}), "function");
  startHermesGatewayTurnDetector({})(); // must not throw
  startHermesGatewayTurnDetector({ intervalMs: 0, readGatewayStatus: async () => "x" })();
});
