#!/usr/bin/env node
import assert from "node:assert/strict";
import {
  pidsForResumeHandle,
  reapPriorManagedClaude,
  parseProcLines,
} from "../reap-managed-claude.js";

const HANDLE = "f9d6f5a4-343d-43a7-9329-bae1694cba06";
const OTHER = "502989ba-6e2f-4358-95e0-1a4b340c2579";

// Model the real win32 process table: many claude.exe, several resuming the
// same agent handle, one resuming a different agent, one with no --resume.
const PROCS = [
  { pid: 100, commandLine: `C:\\claude.exe server:aify-comms-channel --resume ${HANDLE}` },
  { pid: 200, commandLine: `C:\\claude.exe --model opus --resume ${HANDLE} --effort high` },
  { pid: 300, commandLine: `C:\\claude.exe --resume ${OTHER}` }, // different agent
  { pid: 400, commandLine: `C:\\claude.exe --dangerously-skip-permissions` }, // no resume
  { pid: 500, commandLine: `C:\\claude.exe --resume=${HANDLE}` }, // = form
];

// 1. pidsForResumeHandle matches only this agent's instances (space + = forms).
{
  const pids = pidsForResumeHandle(PROCS, HANDLE).sort((a, b) => a - b);
  assert.deepEqual(pids, [100, 200, 500], "must match all instances of this handle, both --resume <h> and --resume=<h>");
  assert.ok(!pids.includes(300), "must NOT match a different agent's handle");
  assert.ok(!pids.includes(400), "must NOT match a no-resume process");
}

// 2. Empty/whitespace handle is a no-op (never mass-kills).
{
  assert.deepEqual(pidsForResumeHandle(PROCS, ""), [], "empty handle matches nothing");
  assert.deepEqual(pidsForResumeHandle(PROCS, "   "), [], "blank handle matches nothing");
}

// 3. A longer handle that merely CONTAINS the target must not be matched.
{
  const procs = [{ pid: 9, commandLine: `claude --resume ${HANDLE}-extra` }];
  assert.deepEqual(pidsForResumeHandle(procs, HANDLE), [], "boundary: prefix of a longer id must not match");
}

// 4. reapPriorManagedClaude kills all instances of the handle, respecting keepPid.
{
  const killed = [];
  const res = reapPriorManagedClaude(HANDLE, {
    keepPid: 200,
    list: () => PROCS,
    kill: (pid) => { killed.push(pid); return true; },
  });
  assert.deepEqual(killed.sort((a, b) => a - b), [100, 500], "kills every instance of the handle EXCEPT keepPid");
  assert.deepEqual(res.killed.sort((a, b) => a - b), [100, 500]);
  assert.ok(!killed.includes(200), "keepPid (the live console) is preserved");
  assert.ok(!killed.includes(300), "other agent untouched");
}

// 5. keepPid=0 reaps ALL instances of the handle (clean-slate before a fresh spawn).
{
  const killed = [];
  reapPriorManagedClaude(HANDLE, { keepPid: 0, list: () => PROCS, kill: (pid) => { killed.push(pid); return true; } });
  assert.deepEqual(killed.sort((a, b) => a - b), [100, 200, 500], "keepPid=0 reaps every instance");
}

// 6. A kill that fails (returns false) is not counted as killed but never throws.
{
  const res = reapPriorManagedClaude(HANDLE, { keepPid: 0, list: () => PROCS, kill: () => false });
  assert.deepEqual(res.candidates.sort((a, b) => a - b), [100, 200, 500], "candidates still reported");
  assert.deepEqual(res.killed, [], "no kills counted when kill() fails");
}

// 7. A throwing list() degrades to a safe no-op (never throws).
{
  const res = reapPriorManagedClaude(HANDLE, { list: () => { throw new Error("ps failed"); }, kill: () => true });
  assert.deepEqual(res.candidates, []);
  assert.deepEqual(res.killed, []);
}

// 8. parseProcLines handles the win "PID\tCMDLINE" format incl. tabs in args.
{
  const parsed = parseProcLines(`1234\tclaude.exe --resume ${HANDLE}\n\n  \nbad-line-no-tab\n5\tclaude.exe x`);
  assert.deepEqual(parsed, [
    { pid: 1234, commandLine: `claude.exe --resume ${HANDLE}` },
    { pid: 5, commandLine: "claude.exe x" },
  ], "parses valid PID\\tCMD lines, skips blanks and tab-less lines");
}

console.log("reap-managed-claude.test.js: all assertions passed");
