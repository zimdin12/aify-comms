#!/usr/bin/env node
// Managed claude: a resume prompt is auto-answered with down+enter exactly once.
// Resident claude is NEVER auto-answered. The kill-switch (autoAnswer:false) disables it.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { TerminalProcessManager } from "../terminal-runtime.js";

const here = dirname(fileURLToPath(import.meta.url));
const resumeFrame = readFileSync(join(here, "fixtures/claude-console/resume-prompt.txt"), "utf8");
const channelFrame = readFileSync(join(here, "fixtures/claude-console/channel-enter.txt"), "utf8");

const tick = (ms = 15) => new Promise((r) => setTimeout(r, ms));

function makeMgr(opts = {}) {
  const typed = [];
  // autoAnswerKeyDelayMs:1 so the spaced down→enter sequence completes fast in the test.
  const mgr = new TerminalProcessManager({ onOutput: async () => {}, autoAnswerKeyDelayMs: 1, ...opts });
  mgr.input = (id, body) => typed.push([id, body]); // stub the PTY write
  return { mgr, typed };
}

// Managed claude: the resume prompt is answered once with a SPACED down→enter sequence
// (so the menu move re-renders before the confirm), even across repeated frames.
{
  const { mgr, typed } = makeMgr();
  const st = { id: "t1", runtime: "claude-code", sessionMode: "managed", agentId: "a1", outputTail: "" };
  mgr.terminals.set("t1", st);
  await mgr._handleOutput("t1", st, resumeFrame);
  await mgr._handleOutput("t1", st, "\r\n❯ 2. Resume full session as-is"); // redraw, still showing
  await tick();
  assert.deepEqual(
    typed.filter((t) => t[0] === "t1").map((t) => t[1]),
    ["\x1b[B", "\r"],
    "managed resume prompt answered exactly once: down, then enter (spaced)",
  );
}

// The same prompt can legitimately appear again. An intervening idle cursor must clear the
// one-shot latch even while the old prompt text remains in the retained terminal tail.
{
  const { mgr, typed } = makeMgr();
  const st = { id: "t5", runtime: "claude-code", sessionMode: "managed", agentId: "a5", outputTail: "" };
  mgr.terminals.set("t5", st);
  await mgr._handleOutput("t5", st, channelFrame);
  await mgr._handleOutput("t5", st, "\n…continued…\n❯ idle input");
  await mgr._handleOutput("t5", st, `\n${channelFrame}`);
  assert.deepEqual(typed.map(([, body]) => body), ["\r", "\r"]);
}

// A spaced menu answer must stop if the prompt disappears between its navigation key and Enter.
{
  const { mgr, typed } = makeMgr({ autoAnswerKeyDelayMs: 20 });
  const st = { id: "t6", runtime: "claude-code", sessionMode: "managed", agentId: "a6", outputTail: "" };
  mgr.terminals.set("t6", st);
  await mgr._handleOutput("t6", st, resumeFrame);
  await mgr._handleOutput("t6", st, "\n…cancelled…\n❯ idle input");
  await tick(30);
  assert.deepEqual(typed.map(([, body]) => body), ["\x1b[B"]);
}

// Resident claude is NEVER auto-answered (no typing into an operator session).
{
  const { mgr, typed } = makeMgr();
  const st = { id: "t2", runtime: "claude-code", sessionMode: "resident", agentId: "a2", outputTail: "" };
  mgr.terminals.set("t2", st);
  await mgr._handleOutput("t2", st, resumeFrame);
  assert.equal(typed.length, 0, "resident session must never be auto-answered");
}

// B1: while claude is mid-turn (consoleClass becomes "working" from the same frame),
// a resume-menu-looking match must NOT be answered — guards against keystroke injection
// into a generating claude. Frame carries BOTH a working footer and resume-ish text.
{
  const { mgr, typed } = makeMgr();
  const st = { id: "t4", runtime: "claude-code", sessionMode: "managed", agentId: "a4", outputTail: "" };
  mgr.terminals.set("t4", st);
  await mgr._handleOutput(
    "t4",
    st,
    "❯ 1. Resume from summary\n  2. Resume full session as-is\n✻ Crunched for 1m 2s (esc to interrupt)",
  );
  assert.equal(typed.length, 0, "must not auto-answer while claude is working (consoleClass=working)");
}

// Kill-switch disables it.
{
  const { mgr, typed } = makeMgr({ autoAnswer: false });
  const st = { id: "t3", runtime: "claude-code", sessionMode: "managed", agentId: "a3", outputTail: "" };
  mgr.terminals.set("t3", st);
  await mgr._handleOutput("t3", st, resumeFrame);
  assert.equal(typed.length, 0, "autoAnswer:false disables auto-answer");
}

console.log("terminal-runtime-auto-answer.test.js: all assertions passed");
