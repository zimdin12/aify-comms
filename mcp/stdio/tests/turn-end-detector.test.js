#!/usr/bin/env node
// Pure tests for the claude hook-independent turn-END detector (pure-event-status
// change #1, 2026-06-02). The claude Stop hook is NOT a guaranteed turn
// terminator (misses on interrupt/ESC, MCP-continuations, crash, or its short
// curl failing), so a turn could stay turn_busy=1 forever. This detector gives
// claude an EVENT-DRIVEN turn-end independent of the Stop hook by watching the
// transcript: when a transcript that WAS growing stops growing for one tick AND
// a turn was in flight, it fires turn-end ONCE. It re-arms on the next growth so
// the next turn is detected too.
//
// ANTI-FEEDBACK-LOOP: the detector keys ONLY on transcript GROWTH (process
// truth), NEVER on the server's computed status — so it cannot self-reinforce.
// It is CONSERVATIVE: it fires turn-end only after a growth phase is followed by
// a no-growth tick, so a single between-tool-calls pause that still shows growth
// never triggers a false clear.
import assert from "node:assert/strict";
import { makeTurnEndDetector } from "../turn-end-detector.js";

// (a) baseline: first observation establishes a baseline, never fires.
{
  const d = makeTurnEndDetector();
  assert.equal(d.observe({ mtimeMs: 100, size: 10 }), false, "first obs is baseline, no fire");
}

// (b) growth ticks (mid-turn) never fire turn-end.
{
  const d = makeTurnEndDetector();
  d.observe({ mtimeMs: 100, size: 10 }); // baseline
  assert.equal(d.observe({ mtimeMs: 130, size: 40 }), false, "growth -> no fire (still working)");
  assert.equal(d.observe({ mtimeMs: 160, size: 90 }), false, "growth -> no fire (still working)");
}

// (c) growth then a no-growth tick fires turn-end exactly ONCE.
{
  const d = makeTurnEndDetector();
  d.observe({ mtimeMs: 100, size: 10 });          // baseline
  assert.equal(d.observe({ mtimeMs: 130, size: 40 }), false, "growth");
  assert.equal(d.observe({ mtimeMs: 160, size: 90 }), false, "growth (final write)");
  assert.equal(d.observe({ mtimeMs: 160, size: 90 }), true, "no growth after growth -> FIRE turn-end once");
  assert.equal(d.observe({ mtimeMs: 160, size: 90 }), false, "already fired -> do NOT fire again (no re-fire)");
}

// (d) re-arm: a NEW growth phase after a fire produces a NEW turn-end on the
//     next no-growth tick (the next turn is detected).
{
  const d = makeTurnEndDetector();
  d.observe({ mtimeMs: 100, size: 10 });          // baseline
  d.observe({ mtimeMs: 130, size: 40 });          // turn 1 growth
  assert.equal(d.observe({ mtimeMs: 130, size: 40 }), true, "turn 1 ends");
  // turn 2 starts: growth resumes
  assert.equal(d.observe({ mtimeMs: 200, size: 80 }), false, "turn 2 growth -> no fire");
  assert.equal(d.observe({ mtimeMs: 200, size: 80 }), true, "turn 2 ends -> FIRE again (re-armed)");
}

// (e) no-growth from baseline (an idle agent that never started a turn) never
//     fires — turn-end only follows an observed growth phase.
{
  const d = makeTurnEndDetector();
  d.observe({ mtimeMs: 100, size: 10 });          // baseline
  assert.equal(d.observe({ mtimeMs: 100, size: 10 }), false, "idle (no growth ever) -> no fire");
  assert.equal(d.observe({ mtimeMs: 100, size: 10 }), false, "still idle -> no fire");
}

// (f) full streaming-then-idle sequence: exactly one fire, on the first
//     no-growth tick after the final write.
{
  const d = makeTurnEndDetector();
  const obs = [
    { mtimeMs: 100, size: 10 },  // baseline
    { mtimeMs: 130, size: 40 },  // streaming
    { mtimeMs: 160, size: 90 },  // streaming
    { mtimeMs: 200, size: 120 }, // final write (still growth)
    { mtimeMs: 200, size: 120 }, // idle -> FIRE
    { mtimeMs: 200, size: 120 }, // idle -> no re-fire
    { mtimeMs: 200, size: 120 }, // idle -> no re-fire
  ];
  const fires = obs.map((o) => d.observe(o));
  assert.deepEqual(
    fires,
    [false, false, false, false, true, false, false],
    "exactly one turn-end fire on the first no-growth tick after the growth phase",
  );
}

// (g) unreadable/sentinel observations (null / zero mtime) never fire — a
//     transient stat failure must not be read as 'turn ended'.
{
  const d = makeTurnEndDetector();
  d.observe({ mtimeMs: 100, size: 10 });
  d.observe({ mtimeMs: 130, size: 40 }); // growth, turn in flight
  assert.equal(d.observe(null), false, "null obs -> no fire (defensive)");
  assert.equal(d.observe({ mtimeMs: 0, size: 0 }), false, "zero obs -> no fire (defensive)");
}

console.log("turn-end-detector.test.js: all assertions passed");
