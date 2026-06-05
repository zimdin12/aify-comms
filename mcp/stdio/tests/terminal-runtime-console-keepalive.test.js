#!/usr/bin/env node
// Managed-claude repaint keepalive: claude only re-emits its spinner footer while its PTY is
// actively rendered, so an UNWATCHED working claude goes quiet on the PTY and the console-working
// lease goes stale -> `online`. _armConsoleKeepalive periodically SIGWINCHes (resize) the PTY so
// claude keeps emitting its footer whether or not the operator watches.
import assert from "node:assert/strict";
import { TerminalProcessManager } from "../terminal-runtime.js";

const tick = (ms = 25) => new Promise((r) => setTimeout(r, ms));

// (1) Only claude-code managed PTYs get the keepalive; it resizes on a cadence.
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
  assert.ok(resizes.length >= 1, "claude managed pty is poked at least once");
  assert.deepEqual(resizes[0], [100, 28], "poke resizes to the same dims (invisible SIGWINCH)");
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

console.log("terminal-runtime-console-keepalive.test.js: all assertions passed");
