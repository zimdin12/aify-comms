#!/usr/bin/env node
// Managed-claude repaint keepalive: claude only re-emits its spinner footer while its PTY is
// actively rendered, so an UNWATCHED working claude goes quiet on the PTY and the console-working
// lease goes stale -> `online`. _armConsoleKeepalive periodically SIGWINCHes (resize) the PTY so
// claude keeps emitting its footer whether or not the operator watches.
import assert from "node:assert/strict";
import { TerminalProcessManager } from "../terminal-runtime.js";

const tick = (ms = 25) => new Promise((r) => setTimeout(r, ms));

// (1) Only claude-code managed PTYs get the keepalive; each tick TOGGLES a dimension so a real
// SIGWINCH fires (a same-dims resize sends NO SIGWINCH — verified empirically), then RESTORES the
// true dims so the net terminal size is unchanged.
{
  const resizes = [];
  const mgr = new TerminalProcessManager({ onOutput: async () => {}, consoleKeepaliveMs: 5 });
  const claude = {
    id: "t1", runtime: "claude-code", sessionMode: "managed", kind: "pty",
    cols: 100, rows: 28, term: { resize: (c, r) => resizes.push([c, r]) },
  };
  mgr.terminals.set("t1", claude);
  const stop = mgr._armConsoleKeepalive("t1", claude);
  await tick();
  stop();
  assert.ok(resizes.length >= 2, "claude managed pty is poked (toggle = 2 resizes per tick)");
  // First tick: a changed dim then a restore to the true dims.
  assert.notDeepEqual(resizes[0], [100, 28], "first resize changes a dim (forces SIGWINCH)");
  assert.deepEqual(resizes[1], [100, 28], "second resize restores the true dims");
  // stop() must halt further pokes.
  const after = resizes.length;
  await tick();
  assert.equal(resizes.length, after, "no pokes after stop()");
}

// (2) non-claude managed pty -> no keepalive armed (returns a noop, never resizes).
{
  const mgr = new TerminalProcessManager({ onOutput: async () => {}, consoleKeepaliveMs: 5 });
  const codex = {
    id: "t2", runtime: "codex", sessionMode: "managed", kind: "pty",
    cols: 100, rows: 28, term: { resize: () => assert.fail("must not poke codex") },
  };
  mgr.terminals.set("t2", codex);
  const stop = mgr._armConsoleKeepalive("t2", codex);
  await tick();
  stop();
}

// (3) resident claude -> no keepalive (never type/poke an operator session).
{
  const mgr = new TerminalProcessManager({ onOutput: async () => {}, consoleKeepaliveMs: 5 });
  const resident = {
    id: "t3", runtime: "claude-code", sessionMode: "resident", kind: "pty",
    cols: 100, rows: 28, term: { resize: () => assert.fail("must not poke resident claude") },
  };
  mgr.terminals.set("t3", resident);
  const stop = mgr._armConsoleKeepalive("t3", resident);
  await tick();
  stop();
}

// (4) keepalive disabled (consoleKeepaliveMs:0) -> noop even for managed claude.
{
  const mgr = new TerminalProcessManager({ onOutput: async () => {}, consoleKeepaliveMs: 0 });
  const claude = {
    id: "t4", runtime: "claude-code", sessionMode: "managed", kind: "pty",
    cols: 100, rows: 28, term: { resize: () => assert.fail("must not poke when disabled") },
  };
  mgr.terminals.set("t4", claude);
  const stop = mgr._armConsoleKeepalive("t4", claude);
  await tick();
  stop();
}

// (5) terminal removed mid-flight -> tick is a safe noop (no throw).
{
  const mgr = new TerminalProcessManager({ onOutput: async () => {}, consoleKeepaliveMs: 5 });
  const claude = {
    id: "t5", runtime: "claude-code", sessionMode: "managed", kind: "pty",
    cols: 100, rows: 28, term: { resize: () => {} },
  };
  mgr.terminals.set("t5", claude);
  const stop = mgr._armConsoleKeepalive("t5", claude);
  mgr.terminals.delete("t5");
  await tick();
  stop();
}

// (6) IDLE-GRACE GATE (#224): a console sustained at the IDLE prompt drops to a SLOW re-probe
// cadence after the grace (it must NEVER fully stop -- a full stop could never re-discover work
// that resumes after a long idle, since an unwatched claude stays quiet and never re-emits a
// working footer -> the console-working lease lapses -> false `online`). A working/unknown class
// keeps full-rate nudging; a flip back off "idle" re-arms full rate.
//
// DRIVEN, NOT TIMED. This section and (7) used to sleep 40-80ms and count how many times a 5ms
// setInterval had fired. Windows floors timers at ~15.6ms, so a 60ms window bought 3 or 4 ticks
// where the assertions needed 4 AND needed one to land on a multiple of the re-probe period.
// Measured 2026-09-01: 2 failures in 6 runs of this file alone, and it had just reddened a full
// bridge suite. The product was never racy; the test budget was below its own cost. Calling the
// tick directly makes each assertion a statement about the GATE rather than about the clock, and
// the exact tick counts below are now the thing under test rather than an approximation of it.
{
  const resizes = [];
  const mgr = new TerminalProcessManager({
    onOutput: async () => {}, consoleKeepaliveMs: 5,
    consoleKeepaliveIdleGraceTicks: 3, consoleKeepaliveIdleReprobeTicks: 4,
  });
  const claude = {
    id: "t6", runtime: "claude-code", sessionMode: "managed", kind: "pty",
    cols: 100, rows: 28, consoleClass: "working", term: { resize: (c, r) => resizes.push([c, r]) },
  };
  mgr.terminals.set("t6", claude);
  const run = (n) => { for (let i = 0; i < n; i += 1) mgr._consoleKeepaliveTick("t6"); };

  run(3);
  assert.equal(resizes.length, 6, "a working console is nudged on every tick (2 resizes each)");

  // Ticks 1-3 are within the grace and still nudge; 4 onwards are past it and only a multiple of
  // the re-probe period fires. Exactly one of ticks 4-8 qualifies: tick 8.
  claude.consoleClass = "idle";
  resizes.length = 0;
  run(3);
  assert.equal(resizes.length, 6, "the grace ticks themselves still nudge");
  // Ticks 4-8. The skip is `streak > grace AND streak % reprobe !== 0`, so tick 4 clears the grace
  // and IS a re-probe multiple -- it nudges. Ticks 5, 6, 7 are skipped; tick 8 nudges. Two nudges,
  // four resizes. (I first wrote 2 resizes here, having counted tick 4 as skipped: it satisfies both
  // halves of the condition at once, which is exactly the boundary a driven test can pin and a timed
  // one could only average over.)
  resizes.length = 0;
  run(5);
  assert.equal(resizes.length, 4, "past the grace only re-probe ticks fire -- and they DO fire (#224)");

  // A full stop is the bug this gate was rewritten to remove, so re-probing forever is the
  // property, not merely re-probing once.
  resizes.length = 0;
  run(20);
  assert.ok(resizes.length >= 8, `re-probing continues indefinitely (got ${resizes.length})`);

  // Work resumes: the streak resets on the very first non-idle tick, so full rate is immediate.
  claude.consoleClass = "working";
  resizes.length = 0;
  run(5);
  assert.equal(resizes.length, 10, "resumed work re-arms full-rate nudging at once -- #224 fix");
  assert.equal(claude._kaIdleTicks, 0, "the idle streak is cleared, not decayed");
}

// (7) CONSOLE-CLASS FLAP (status-accuracy Task 2): a managed-claude console whose consoleClass
// flaps working -> unknown -> working across ticks must KEEP getting nudged -- only a SUSTAINED
// idle run (> grace) pauses. The idle accumulator resets to 0 on ANY non-idle class (working AND
// unknown), so a transient mid-turn unknown blip can never accumulate toward the idle-grace pause
// and drop the keepalive on a still-working turn (which would let the 20s console-working lease
// lapse -> false `online`). Guards against the SIGWINCH-keepalive-misfire-on-flap regression.
{
  const resizes = [];
  const mgr = new TerminalProcessManager({
    onOutput: async () => {}, consoleKeepaliveMs: 5,
    consoleKeepaliveIdleGraceTicks: 3, consoleKeepaliveIdleReprobeTicks: 4,
  });
  const claude = {
    id: "t7", runtime: "claude-code", sessionMode: "managed", kind: "pty",
    cols: 100, rows: 28, consoleClass: "working", term: { resize: (c, r) => resizes.push([c, r]) },
  };
  mgr.terminals.set("t7", claude);
  const run = (n) => { for (let i = 0; i < n; i += 1) mgr._consoleKeepaliveTick("t7"); };

  // A flap held far longer than the grace on each class. Never a SUSTAINED idle, so never throttled.
  for (const cls of ["working", "unknown", "working", null, "working"]) {
    claude.consoleClass = cls;
    run(4);
  }
  assert.equal(resizes.length, 40, "a working/unknown flap is nudged on every one of its 20 ticks");
  assert.equal(claude._kaIdleTicks, 0, "no non-idle class ever accumulates toward the grace");

  // An `idle` interleaved with non-idle also never accumulates -- one non-idle tick resets it.
  resizes.length = 0;
  for (let i = 0; i < 6; i += 1) {
    claude.consoleClass = "idle"; run(1);
    claude.consoleClass = "working"; run(1);
  }
  assert.equal(resizes.length, 24, "an idle that never sustains is never throttled");

  // Sustained idle DOES throttle, which is what proves the flap above was not simply un-gated.
  claude.consoleClass = "idle";
  claude._kaIdleTicks = 0;
  resizes.length = 0;
  run(20);
  // Ticks 1-3 are inside the grace and nudge; then 4, 8, 12, 16, 20. Eight nudges, sixteen resizes.
  assert.equal(resizes.length, 16,
    "sustained idle throttles to grace ticks plus re-probes, and never stops -- #224");
  assert.ok(resizes.length < 40, "throttled is well below the full-rate flap");
}

console.log("terminal-runtime-console-keepalive.test.js: all assertions passed");
