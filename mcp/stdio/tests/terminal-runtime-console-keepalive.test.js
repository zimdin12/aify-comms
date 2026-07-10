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

// (2b) managed HERMES pty -> DOES get the keepalive (2026-07-10 blank-hermes-console fix).
// A hermes visible TUI paints its screen once then only emits tiny cursor-positioned diffs, so
// the 64KB raw-log tail has no full frame and the console snapshot came out blank. SIGWINCH forces
// a full repaint, keeping a fresh frame in the captured window — same mechanism claude already uses.
{
  const resizes = [];
  const mgr = new TerminalProcessManager({ onOutput: async () => {}, consoleKeepaliveMs: 5 });
  const hermes = {
    id: "t2b", runtime: "hermes", sessionMode: "managed", kind: "pty",
    cols: 100, rows: 28, term: { resize: (c, r) => resizes.push([c, r]) },
  };
  mgr.terminals.set("t2b", hermes);
  const stop = mgr._armConsoleKeepalive("t2b", hermes);
  await tick();
  stop();
  assert.ok(resizes.length >= 2, "managed hermes pty is poked (toggle = 2 resizes per tick)");
  assert.notDeepEqual(resizes[0], [100, 28], "first resize changes a dim (forces SIGWINCH → hermes repaint)");
  assert.deepEqual(resizes[1], [100, 28], "second resize restores the true dims");
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
// cadence after the grace (it must NEVER fully stop — a full stop could never re-discover work
// that resumes after a long idle, since an unwatched claude stays quiet and never re-emits a
// working footer → the console-working lease lapses → false `online`). A working/unknown class
// keeps full-rate nudging; a flip back off "idle" re-arms full rate.
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
  const stop = mgr._armConsoleKeepalive("t6", claude);
  await tick(40);                                  // working → full-rate poking
  assert.ok(resizes.length >= 2, "a working console keeps getting nudged");
  claude.consoleClass = "idle";                    // genuinely idle now
  await tick(60);                                  // > 3 grace ticks of idle → drop to slow re-probe
  const afterGrace = resizes.length;
  await tick(60);                                  // a slow-re-probe window
  const slowDelta = resizes.length - afterGrace;
  // #224 guard: the keepalive must STAY ALIVE on a sustained-idle console (re-probe), never the
  // old full stop — otherwise resumed work after a long idle is never re-detected.
  assert.ok(slowDelta > 0, "a sustained-idle console still re-probes (never fully stops) — #224");
  claude.consoleClass = "working";                 // work resumes → must re-arm full rate
  const beforeResume = resizes.length;
  await tick(60);                                  // same-length window, now full rate
  const resumeDelta = resizes.length - beforeResume;
  assert.ok(resumeDelta > slowDelta,
    `resumed work re-arms full-rate nudging (resume ${resumeDelta} > idle re-probe ${slowDelta}) — #224 fix`);
  stop();
}

// (7) CONSOLE-CLASS FLAP (status-accuracy Task 2): a managed-claude console whose consoleClass
// flaps working → unknown → working across ticks must KEEP getting nudged — only a SUSTAINED idle
// run (> grace) pauses. The idle accumulator resets to 0 on ANY non-idle class (working AND
// unknown), so a transient mid-turn unknown blip can never accumulate toward the idle-grace pause
// and drop the keepalive on a still-working turn (which would let the 20s console-working lease
// lapse → false `online`). Guards against the SIGWINCH-keepalive-misfire-on-flap regression.
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
  const stop = mgr._armConsoleKeepalive("t7", claude);
  // Drive a working → unknown → working flap across several ticks, holding each class long enough
  // to span more than `consoleKeepaliveIdleGraceTicks` worth of ticks — far longer than the grace
  // a SUSTAINED idle would need to pause. A flapping (never-sustained-idle) console must NOT pause.
  await tick(40);                                  // working
  claude.consoleClass = "unknown"; await tick(40); // unknown (could be working — keep nudging)
  claude.consoleClass = "working"; await tick(40); // working again
  claude.consoleClass = null;      await tick(40); // null/unknown class
  claude.consoleClass = "working"; await tick(40); // working again
  const flapResizes = resizes.length;
  assert.ok(flapResizes >= 8, `a working↔unknown flap keeps getting nudged (got ${flapResizes})`);
  // Now a genuinely SUSTAINED idle must THROTTLE to the slow re-probe cadence (proves the gate
  // still works — the flap above kept full rate). It must not fully stop (#224): re-probe stays
  // alive but at a fraction of the full-rate flap above.
  claude.consoleClass = "idle";
  await tick(80);                                  // >> 3 grace ticks of sustained idle → throttle
  const throttledFrom = resizes.length;
  await tick(80);                                  // a slow-re-probe window
  const throttledDelta = resizes.length - throttledFrom;
  assert.ok(throttledDelta > 0, "sustained idle still re-probes (never fully stops) — #224");
  assert.ok(throttledDelta * 2 < flapResizes,
    `sustained idle throttles well below the full-rate flap (throttled ${throttledDelta} vs flap ${flapResizes})`);
  stop();
}

console.log("terminal-runtime-console-keepalive.test.js: all assertions passed");
