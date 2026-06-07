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

// (6) IDLE-GRACE GATE: a console sustained at the IDLE prompt stops getting nudged after the
// grace; a working/unknown class keeps getting nudged; a flip back off "idle" re-arms.
{
  const resizes = [];
  const mgr = new TerminalProcessManager({
    onOutput: async () => {}, consoleKeepaliveMs: 5, consoleKeepaliveIdleGraceTicks: 3,
  });
  const claude = {
    id: "t6", runtime: "claude-code", sessionMode: "managed", kind: "pty",
    cols: 100, rows: 28, consoleClass: "working", term: { resize: (c, r) => resizes.push([c, r]) },
  };
  mgr.terminals.set("t6", claude);
  const stop = mgr._armConsoleKeepalive("t6", claude);
  await tick(40);                                  // working → keeps poking
  assert.ok(resizes.length >= 2, "a working console keeps getting nudged");
  claude.consoleClass = "idle";                    // genuinely idle now
  await tick(60);                                  // > 3 grace ticks of idle
  const pausedAt = resizes.length;
  await tick(40);
  assert.equal(resizes.length, pausedAt, "a sustained-idle console stops getting nudged (no churn)");
  claude.consoleClass = "working";                 // new turn → output reclassifies off idle
  await tick(40);
  assert.ok(resizes.length > pausedAt, "a flip back off idle (new turn) re-arms the keepalive");
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
    onOutput: async () => {}, consoleKeepaliveMs: 5, consoleKeepaliveIdleGraceTicks: 3,
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
  // Now a genuinely SUSTAINED idle must pause (proves the gate still works — the flap above wasn't
  // ignoring the gate entirely).
  claude.consoleClass = "idle";
  await tick(80);                                  // >> 3 grace ticks of sustained idle
  const pausedAt = resizes.length;
  await tick(40);
  assert.equal(resizes.length, pausedAt, "only SUSTAINED idle pauses the keepalive (the flap did not)");
  stop();
}

console.log("terminal-runtime-console-keepalive.test.js: all assertions passed");
