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

function makeMgr(opts = {}) {
  const typed = [];
  const mgr = new TerminalProcessManager({ onOutput: async () => {}, ...opts });
  mgr.input = (id, body) => typed.push([id, body]); // stub the PTY write
  return { mgr, typed };
}

// Managed claude: answered once even across repeated frames that still show the menu.
{
  const { mgr, typed } = makeMgr();
  const st = { id: "t1", runtime: "claude-code", sessionMode: "managed", agentId: "a1", outputTail: "" };
  mgr.terminals.set("t1", st);
  await mgr._handleOutput("t1", st, resumeFrame);
  await mgr._handleOutput("t1", st, "\r\n❯ 2. Resume full session as-is"); // redraw, still showing
  assert.deepEqual(
    typed.filter((t) => t[0] === "t1").map((t) => t[1]),
    ["\x1b[B\r"],
    "managed resume prompt answered exactly once with down+enter",
  );
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
