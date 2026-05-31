#!/usr/bin/env node
import assert from "node:assert/strict";
import {
  pidsForResumeHandle,
  procsForResumeHandle,
  parentBelongsToAgent,
  reapPriorManagedClaude,
  parseProcLines,
} from "../reap-managed-claude.js";

const HANDLE = "f9d6f5a4-343d-43a7-9329-bae1694cba06";
const SHARED = "651b895f-a564-4d3a-8e0b-27f8429b1dd0"; // the collision from the incident
const OTHER = "502989ba-6e2f-4358-95e0-1a4b340c2579";

// Process table: each claude.exe has a ppid pointing at its claude-aify wrapper.
// Wrappers are looked up via getCmdline(ppid).
const PROCS = [
  { pid: 100, ppid: 11, commandLine: `claude.exe server:aify-comms-channel --resume ${HANDLE}` },
  { pid: 200, ppid: 12, commandLine: `claude.exe --model opus --resume ${HANDLE}` },
  { pid: 300, ppid: 13, commandLine: `claude.exe --resume ${OTHER}` },
  // The INCIDENT: two agents share SHARED handle — one managed (sc-coder), one
  // the resident operator session (comms-tech-lead).
  { pid: 400, ppid: 14, commandLine: `claude.exe --resume ${SHARED}` }, // managed sc-coder
  { pid: 500, ppid: 15, commandLine: `claude.exe --resume ${SHARED}` }, // RESIDENT operator
];
const WRAPPERS = {
  11: `bash claude-aify --aify-agent sc-coder --auto --resume ${HANDLE}`,
  12: `bash claude-aify --aify-agent sc-coder --auto --resume ${HANDLE}`,
  13: `bash claude-aify --aify-agent other-agent --auto --resume ${OTHER}`,
  14: `bash claude-aify --aify-agent sc-coder --auto --resume ${SHARED}`,
  15: `bash claude-aify --aify-agent comms-tech-lead --resume ${SHARED}`, // resident, different agent
};
const getCmdline = (pid) => WRAPPERS[pid] || "";

// 1. procsForResumeHandle / pidsForResumeHandle match by handle (unchanged).
{
  assert.deepEqual(pidsForResumeHandle(PROCS, HANDLE).sort((a, b) => a - b), [100, 200]);
  assert.deepEqual(pidsForResumeHandle(PROCS, SHARED).sort((a, b) => a - b), [400, 500]);
  assert.deepEqual(pidsForResumeHandle(PROCS, ""), []);
}

// 2. parentBelongsToAgent matches --aify-agent (space + = forms), with boundary.
{
  assert.ok(parentBelongsToAgent("bash claude-aify --aify-agent sc-coder --auto", "sc-coder"));
  assert.ok(parentBelongsToAgent("claude-aify --aify-agent=sc-coder", "sc-coder"));
  assert.ok(!parentBelongsToAgent("claude-aify --aify-agent sc-coder-2", "sc-coder"), "boundary: longer id must not match");
  assert.ok(!parentBelongsToAgent("", "sc-coder"));
  assert.ok(!parentBelongsToAgent("claude-aify --aify-agent sc-coder", ""), "empty agent never matches");
}

// 3. THE INCIDENT REGRESSION: reaping sc-coder on the SHARED handle must kill
//    ONLY sc-coder's claude (400), never the resident operator session (500),
//    even though both share --resume 651b895f.
{
  const killed = [];
  const res = reapPriorManagedClaude(SHARED, {
    agentId: "sc-coder",
    list: () => PROCS,
    getCmdline,
    kill: (pid) => { killed.push(pid); return true; },
  });
  assert.deepEqual(killed, [400], "MUST kill only sc-coder's instance, NOT the resident operator (500)");
  assert.deepEqual(res.killed, [400]);
  assert.ok(res.skipped.some((s) => s.pid === 500), "the resident operator session is explicitly skipped");
  assert.ok(!killed.includes(500), "NEVER kill the operator's resident session on a handle collision");
}

// 4. Normal case: reaping sc-coder on its own handle kills its managed instances.
{
  const killed = [];
  reapPriorManagedClaude(HANDLE, {
    agentId: "sc-coder", keepPid: 200, list: () => PROCS, getCmdline,
    kill: (pid) => { killed.push(pid); return true; },
  });
  assert.deepEqual(killed, [100], "kills sc-coder instances except keepPid; 200 preserved, 300/other untouched");
}

// 5. FAIL-SAFE: no agentId → kill nothing.
{
  const killed = [];
  const res = reapPriorManagedClaude(HANDLE, { list: () => PROCS, getCmdline, kill: (pid) => { killed.push(pid); return true; } });
  assert.deepEqual(killed, [], "no agentId → fail-safe, kills nothing");
  assert.ok(res.skipped.some((s) => /no agentId/i.test(s.reason)));
}

// 6. Unknown/dead parent (orphan) → NOT killed (fail-safe toward leaking).
{
  const killed = [];
  reapPriorManagedClaude(HANDLE, {
    agentId: "sc-coder", list: () => PROCS, getCmdline: () => "", // parent unknown
    kill: (pid) => { killed.push(pid); return true; },
  });
  assert.deepEqual(killed, [], "unconfirmable parent → not killed (fail-safe)");
}

// 7. Throwing list() degrades safely.
{
  const res = reapPriorManagedClaude(HANDLE, { agentId: "sc-coder", list: () => { throw new Error("ps fail"); }, getCmdline, kill: () => true });
  assert.deepEqual(res.killed, []);
}

// 8. parseProcLines: PID\tPPID\tCMDLINE.
{
  const parsed = parseProcLines(`1234\t11\tclaude.exe --resume ${HANDLE}\n\nbad\n5\t6\tclaude.exe x`);
  assert.deepEqual(parsed, [
    { pid: 1234, ppid: 11, commandLine: `claude.exe --resume ${HANDLE}` },
    { pid: 5, ppid: 6, commandLine: "claude.exe x" },
  ]);
}

console.log("reap-managed-claude.test.js: all assertions passed");
