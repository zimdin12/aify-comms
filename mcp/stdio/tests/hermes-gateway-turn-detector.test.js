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

test("startHermesGatewayTurnDetector: re-stamps turn-busy while WORKING (long turn never goes stale)", async () => {
  // The bug: turn-start is edge-triggered (once), so a turn longer than the dispatch
  // re-pulse window let the server expire turn_busy → `online` while still working
  // (next-senior-dev long refactor). While the gateway stays WORKING, the detector must
  // keep re-stamping turn-busy so last_event_at never crosses the server stale window.
  let starts = 0;
  // Intervals are kept well ABOVE the Windows ~15ms setInterval timer floor: at intervalMs:5
  // Windows fires ticks ~every 15ms, so the nominal-intervalMs accumulator under-counts and
  // the refresh barely fires (a deterministic cross-platform failure, not flakiness). 25ms
  // ticks + a proportional window keep this meaningful on every OS. (Production uses
  // intervalMs~3000 / workingRefreshMs~45000, where the 15ms floor is irrelevant.)
  const stop = startHermesGatewayTurnDetector({
    intervalMs: 25,
    idleDebounce: 3,
    workingRefreshMs: 50, // refresh ~every 2 ticks while working
    readGatewayStatus: async () => "working", // one long, uninterrupted turn
    postTurnStart: async () => { starts++; },
    postTurnEnd: async () => {},
  });
  await new Promise((r) => setTimeout(r, 300));
  stop();
  // 1 edge "start" + several periodic refreshes — NOT a single edge that then goes stale.
  assert.ok(starts >= 3, `a sustained working turn must keep re-stamping turn-busy (got ${starts})`);
});

test("startHermesGatewayTurnDetector: workingRefreshMs=0 keeps edge-only turn-start", async () => {
  let starts = 0;
  const stop = startHermesGatewayTurnDetector({
    intervalMs: 5,
    idleDebounce: 3,
    workingRefreshMs: 0, // disabled → edge-triggered only (back-compat)
    readGatewayStatus: async () => "working",
    postTurnStart: async () => { starts++; },
    postTurnEnd: async () => {},
  });
  await new Promise((r) => setTimeout(r, 50));
  stop();
  assert.equal(starts, 1, "refresh disabled → exactly one edge /turn-start");
});

// ---------------------------------------------------------------------------
// shouldFireTurnStart gate (2026-07-10 flap fix): managed hermes suppresses a
// /turn-start when NO dispatched turn is open, so hermes POST-TURN background
// model work (self-improvement / memory) — which also sets gateway working — does
// not re-fire `working` on an idle-to-the-user agent. Default: always fire
// (resident / back-compat). Turn-END is NEVER gated (it only clears).
// ---------------------------------------------------------------------------

test("shouldFireTurnStart=false SUPPRESSES the edge /turn-start (background gateway 'working' → no flap)", async () => {
  let starts = 0, ends = 0;
  const stop = startHermesGatewayTurnDetector({
    intervalMs: 5,
    idleDebounce: 2,
    readGatewayStatus: async () => "working", // background self-improvement: gateway running, no dispatch
    postTurnStart: async () => { starts++; },
    postTurnEnd: async () => { ends++; },
    shouldFireTurnStart: () => false, // no dispatched turn open
  });
  await new Promise((r) => setTimeout(r, 40));
  stop();
  assert.equal(starts, 0, "background working must NOT fire /turn-start when no dispatched turn is open");
  assert.equal(ends, 0, "no /turn-end while working");
});

test("shouldFireTurnStart=false ALSO suppresses the working-refresh keep-alive", async () => {
  let starts = 0;
  const stop = startHermesGatewayTurnDetector({
    intervalMs: 25,
    idleDebounce: 3,
    workingRefreshMs: 50, // would refresh every ~2 ticks if allowed
    readGatewayStatus: async () => "working",
    postTurnStart: async () => { starts++; },
    postTurnEnd: async () => {},
    shouldFireTurnStart: () => false,
  });
  await new Promise((r) => setTimeout(r, 300));
  stop();
  assert.equal(starts, 0, "the keep-alive must not re-stamp turn-busy for post-turn background work");
});

test("shouldFireTurnStart=true fires normally (dispatched turn open → real turn shows working)", async () => {
  let starts = 0;
  const stop = startHermesGatewayTurnDetector({
    intervalMs: 5,
    idleDebounce: 2,
    readGatewayStatus: async () => "working",
    postTurnStart: async () => { starts++; },
    postTurnEnd: async () => {},
    shouldFireTurnStart: () => true, // dispatched turn open
  });
  await new Promise((r) => setTimeout(r, 40));
  stop();
  assert.equal(starts, 1, "an open dispatched turn fires /turn-start exactly once on the working edge");
});

test("shouldFireTurnStart gate is dynamic: fires while open, then a NEW background 'working' after end is suppressed", async () => {
  // Models the real lifecycle: dispatch open (fire), turn ends (credit revoked),
  // then post-turn background working must NOT re-fire. The gate is read live each tick.
  let open = true;
  let starts = 0, ends = 0;
  // working (turn) → idle,idle (end, revokes credit) → working (background, suppressed)
  const statuses = ["working", "working", "idle", "idle", "working", "working", "working"];
  let i = 0;
  const stop = startHermesGatewayTurnDetector({
    intervalMs: 5,
    idleDebounce: 2,
    readGatewayStatus: async () => statuses[Math.min(i++, statuses.length - 1)],
    postTurnStart: async () => { starts++; },
    // Mirror the managed-host wiring: turn-end revokes the credit.
    postTurnEnd: async () => { ends++; open = false; },
    shouldFireTurnStart: () => open,
  });
  await new Promise((r) => setTimeout(r, 90));
  stop();
  assert.equal(starts, 1, "exactly one /turn-start — for the dispatched turn; the post-end background working is suppressed");
  assert.equal(ends, 1, "the dispatched turn still ends normally on sustained idle");
});

test("turn-END is NEVER gated by shouldFireTurnStart (a stuck turn_busy must always be clearable)", async () => {
  let ends = 0;
  const statuses = ["working", "idle", "idle", "idle"];
  let i = 0;
  const stop = startHermesGatewayTurnDetector({
    intervalMs: 5,
    idleDebounce: 2,
    readGatewayStatus: async () => statuses[Math.min(i++, statuses.length - 1)],
    postTurnStart: async () => {},
    postTurnEnd: async () => { ends++; },
    shouldFireTurnStart: () => false, // even fully suppressed starts...
  });
  await new Promise((r) => setTimeout(r, 60));
  stop();
  assert.equal(ends, 1, "...sustained idle still fires /turn-end (clear is never gated)");
});

test("startHermesGatewayTurnDetector: refresh does not cause a false turn-end, and still ends on sustained idle", async () => {
  // working (refreshing) then sustained idle → exactly one end; refresh must not interfere.
  let ends = 0, i = 0;
  // Intervals above the Windows ~15ms timer floor (see the re-stamp test above).
  const stop = startHermesGatewayTurnDetector({
    intervalMs: 25,
    idleDebounce: 2,
    workingRefreshMs: 40,
    readGatewayStatus: async () => { i++; return i <= 4 ? "working" : "idle"; },
    postTurnStart: async () => {},
    postTurnEnd: async () => { ends++; },
  });
  await new Promise((r) => setTimeout(r, 350));
  stop();
  assert.equal(ends, 1, "sustained idle after a refreshing working turn → exactly one /turn-end");
});

// ---------------------------------------------------------------------------
// KEEP-CLEARED (2026-07-13): symmetric mirror of KEEP-FRESH. The edge clear only
// fires after THIS detector observed working→idle, so a stray in_turn set outside
// it (a hook/sidecar /turn-start whose end was lost) latched `working` until the
// 30-min ceiling. While the gateway PROVES idle and we're not mid-turn, re-assert
// /turn-end every idleRefreshMs. Gated on a real idle read; never on unknown.
// ---------------------------------------------------------------------------

test("KEEP-CLEARED: sustained gateway IDLE (never saw working) re-asserts /turn-end (stray clear)", async () => {
  // The stray case: idle before ever observing working — the edge clear's submit-race guard
  // never fires an "end", so ONLY keep-cleared can heal a stray in_turn here.
  let ends = 0;
  const stop = startHermesGatewayTurnDetector({
    intervalMs: 25,
    idleDebounce: 1,
    idleRefreshMs: 50,
    readGatewayStatus: async () => "idle",
    postTurnStart: async () => {},
    postTurnEnd: async () => { ends++; },
  });
  await new Promise((r) => setTimeout(r, 300));
  stop();
  assert.ok(ends >= 3, `sustained gateway idle must keep re-asserting turn-end (got ${ends})`);
});

test("KEEP-CLEARED (hermes): never fires while the gateway reports WORKING", async () => {
  let ends = 0, starts = 0;
  const stop = startHermesGatewayTurnDetector({
    intervalMs: 25,
    workingRefreshMs: 50,
    idleRefreshMs: 50,
    readGatewayStatus: async () => "working",
    postTurnStart: async () => { starts++; },
    postTurnEnd: async () => { ends++; },
  });
  await new Promise((r) => setTimeout(r, 300));
  stop();
  assert.equal(ends, 0, `keep-cleared must never fire while working; got ${ends}`);
  assert.ok(starts >= 3, `keep-fresh still re-stamps working; got ${starts}`);
});

test("KEEP-CLEARED (hermes): unknown/'' status never re-asserts turn-end (false-clear safety)", async () => {
  let ends = 0;
  const stop = startHermesGatewayTurnDetector({
    intervalMs: 25,
    idleRefreshMs: 50,
    readGatewayStatus: async () => "", // unknown/transient
    postTurnStart: async () => {},
    postTurnEnd: async () => { ends++; },
  });
  await new Promise((r) => setTimeout(r, 200));
  stop();
  assert.equal(ends, 0, `unknown status is not proof of idle → never keep-clears; got ${ends}`);
});

test("KEEP-CLEARED (hermes): idleRefreshMs=0 disables the re-assert (back-compat)", async () => {
  let ends = 0;
  const stop = startHermesGatewayTurnDetector({
    intervalMs: 5,
    idleDebounce: 1,
    idleRefreshMs: 0,
    readGatewayStatus: async () => "idle", // idle-before-working: edge never ends it
    postTurnStart: async () => {},
    postTurnEnd: async () => { ends++; },
  });
  await new Promise((r) => setTimeout(r, 60));
  stop();
  assert.equal(ends, 0, `disabled keep-cleared + idle-before-working → no turn-end; got ${ends}`);
});
