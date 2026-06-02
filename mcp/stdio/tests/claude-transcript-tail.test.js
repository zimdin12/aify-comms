#!/usr/bin/env node
// Tests for ClaudeAdapter.transcriptTail (pure-event-status change #1 rewrite,
// 2026-06-02). transcriptTail reads the last N lines of THIS agent's OWN session
// .jsonl (same session scoping as transcriptStat — captured store / env, NEVER a
// shared-dir fallback) and returns a small STRUCTURAL summary the turn-end
// detector uses to decide in-flight vs ended:
//   { lastRole, lastStopReason, pendingToolUse }
//
// The fixtures below mirror the REAL claude JSONL schema sampled from a live
// session (2026-06-02): message lines carry message.role / message.stop_reason /
// message.content[].type, and NON-message bookkeeping lines (type "last-prompt",
// "mode", "permission-mode", "attachment", "summary") get appended AFTER the last
// assistant message and MUST be skipped when finding the last real message.
import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { ClaudeAdapter } from "../adapters/claude.js";

const adapter = new ClaudeAdapter();

// claude encodes the project dir as cwd.replace(/[^a-zA-Z0-9]/g,'-')
function encodeCwd(cwd) { return String(cwd).replace(/[^a-zA-Z0-9]/g, "-"); }

// Build a throwaway HOME with a transcript for a given session id + cwd, write
// the given JSONL lines (array of objects), and return { homeDir, cwd, sessionId }.
async function makeTranscript(lines) {
  const homeDir = await fs.mkdtemp(path.join(os.tmpdir(), "claude-tail-"));
  const cwd = "C:/work/proj";
  const sessionId = "11111111-2222-3333-4444-555555555555";
  const projDir = path.join(homeDir, ".claude", "projects", encodeCwd(cwd));
  await fs.mkdir(projDir, { recursive: true });
  const body = lines.map((o) => JSON.stringify(o)).join("\n") + "\n";
  await fs.writeFile(path.join(projDir, `${sessionId}.jsonl`), body, "utf8");
  return { homeDir, cwd, sessionId };
}

function asstMsg(stopReason, contentTypes) {
  return {
    type: "assistant",
    isSidechain: false,
    message: { role: "assistant", stop_reason: stopReason, content: contentTypes.map((t) => ({ type: t })) },
  };
}
function userMsg(contentTypes) {
  return { type: "user", message: { role: "user", content: contentTypes.map((t) => ({ type: t })) } };
}
const lastPrompt = { type: "last-prompt", lastPrompt: "redacted" };
const modeLine = { type: "mode", mode: "default" };
const permLine = { type: "permission-mode", permissionMode: "default" };

// (1) ENDED: last assistant message stop_reason end_turn, trailed by bookkeeping
//     lines (last-prompt/mode/permission-mode) which must be skipped.
{
  const { homeDir, cwd, sessionId } = await makeTranscript([
    userMsg(["text"]),
    asstMsg("end_turn", ["text"]),
    lastPrompt, modeLine, permLine,
  ]);
  const s = await adapter.transcriptTail({ homeDir, cwd, env: { CLAUDE_SESSION_ID: sessionId } });
  assert.equal(s.lastRole, "assistant", "(1) last message role is assistant");
  assert.equal(s.lastStopReason, "end_turn", "(1) stop_reason end_turn");
  assert.equal(s.pendingToolUse, false, "(1) no pending tool_use");
  await fs.rm(homeDir, { recursive: true, force: true });
}

// (2) IN-FLIGHT: last assistant message stop_reason tool_use with a tool_use
//     content block (a long build / a Task sub-agent dispatch).
{
  const { homeDir, cwd, sessionId } = await makeTranscript([
    asstMsg("tool_use", ["thinking"]),
    asstMsg("tool_use", ["text"]),
    asstMsg("tool_use", ["tool_use"]),
  ]);
  const s = await adapter.transcriptTail({ homeDir, cwd, env: { CLAUDE_SESSION_ID: sessionId } });
  assert.equal(s.lastRole, "assistant", "(2) last role assistant");
  assert.equal(s.lastStopReason, "tool_use", "(2) stop_reason tool_use");
  assert.equal(s.pendingToolUse, true, "(2) pending tool_use true");
  await fs.rm(homeDir, { recursive: true, force: true });
}

// (3) IN-FLIGHT: trailing user/tool_result (tool returned, model owes next step).
{
  const { homeDir, cwd, sessionId } = await makeTranscript([
    asstMsg("tool_use", ["tool_use"]),
    userMsg(["tool_result"]),
  ]);
  const s = await adapter.transcriptTail({ homeDir, cwd, env: { CLAUDE_SESSION_ID: sessionId } });
  assert.equal(s.lastRole, "user", "(3) last role user (tool_result)");
  assert.equal(s.pendingToolUse, false, "(3) user line has no pending tool_use of its own");
  await fs.rm(homeDir, { recursive: true, force: true });
}

// (4) unresolved session id -> null (NEVER a shared-dir newest-file fallback).
{
  const homeDir = await fs.mkdtemp(path.join(os.tmpdir(), "claude-tail-"));
  const cwd = "C:/work/proj";
  const projDir = path.join(homeDir, ".claude", "projects", encodeCwd(cwd));
  await fs.mkdir(projDir, { recursive: true });
  // a teammate's transcript exists, but THIS agent has no resolvable session id
  await fs.writeFile(path.join(projDir, "teammate.jsonl"), JSON.stringify(asstMsg("end_turn", ["text"])) + "\n", "utf8");
  const s = await adapter.transcriptTail({ homeDir, cwd, env: {} });
  assert.equal(s, null, "(4) unresolved session id -> null, no shared-dir fallback");
  await fs.rm(homeDir, { recursive: true, force: true });
}

// (5) missing transcript file -> null.
{
  const homeDir = await fs.mkdtemp(path.join(os.tmpdir(), "claude-tail-"));
  const s = await adapter.transcriptTail({ homeDir, cwd: "C:/work/proj", env: { CLAUDE_SESSION_ID: "deadbeef-0000-0000-0000-000000000000" } });
  assert.equal(s, null, "(5) missing file -> null");
  await fs.rm(homeDir, { recursive: true, force: true });
}

// (6) explicit env CLAUDE_SESSION_ID resolves the file when no captured store.
{
  const homeDir = await fs.mkdtemp(path.join(os.tmpdir(), "claude-tail-"));
  const cwd = "C:/work/proj";
  const sid = "abcdef00-1111-2222-3333-444444444444";
  const projDir = path.join(homeDir, ".claude", "projects", encodeCwd(cwd));
  await fs.mkdir(projDir, { recursive: true });
  await fs.writeFile(path.join(projDir, `${sid}.jsonl`), JSON.stringify(asstMsg("end_turn", ["text"])) + "\n", "utf8");
  const s = await adapter.transcriptTail({ homeDir, cwd, env: { CLAUDE_SESSION_ID: sid } });
  assert.equal(s.lastRole, "assistant", "(6) env session id resolves tail");
  assert.equal(s.lastStopReason, "end_turn", "(6) end_turn");
  await fs.rm(homeDir, { recursive: true, force: true });
}

// (7) large file: only the TAIL is read (correctness with many leading lines).
{
  const filler = [];
  for (let i = 0; i < 5000; i++) filler.push(asstMsg("tool_use", ["text"]));
  filler.push(userMsg(["text"]));
  filler.push(asstMsg("end_turn", ["text"]));
  filler.push(lastPrompt);
  const { homeDir, cwd, sessionId } = await makeTranscript(filler);
  const s = await adapter.transcriptTail({ homeDir, cwd, env: { CLAUDE_SESSION_ID: sessionId } });
  assert.equal(s.lastStopReason, "end_turn", "(7) tail of a large file -> last real message");
  assert.equal(s.pendingToolUse, false, "(7) no pending tool_use");
  await fs.rm(homeDir, { recursive: true, force: true });
}

// (8) only bookkeeping lines after start (no message line in tail window) -> the
//     summary still reports lastRole null so the detector treats it as unknown.
{
  const { homeDir, cwd, sessionId } = await makeTranscript([lastPrompt, modeLine, permLine]);
  const s = await adapter.transcriptTail({ homeDir, cwd, env: { CLAUDE_SESSION_ID: sessionId } });
  assert.equal(s.lastRole, null, "(8) no message line -> lastRole null");
  await fs.rm(homeDir, { recursive: true, force: true });
}

console.log("claude-transcript-tail.test.js: all assertions passed");
