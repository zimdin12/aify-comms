#!/usr/bin/env node
// Tests that CALL `proc-probes.js` — the process read side extracted from `reap-managed-survivors.js`
// in v0.5.4.
//
// WHY THIS HALF DESERVES ITS OWN TESTS: identification is what makes an env-scoped reaper safe. A
// survivor is killed only when its own command line says which agent it belongs to, so matching too
// loosely kills a co-located agent's process and matching too tightly leaves an orphan holding a
// session. Both failures are silent — one shows up as an agent that died for no reason, the other as
// a session nobody can reclaim.
//
// `cmdlineDeliveryLoopAgent` and `cmdlineResidentAgent` already had coverage that moved here with
// them. What is new is the boundary cases around them and `parseProcLines`, which turns a
// tab-separated process listing into rows and is the input every match runs against.

import assert from "node:assert/strict";

import {
  cmdlineDeliveryLoopAgent,
  cmdlineResidentAgent,
  defaultListProcesses,
  parseProcLines,
} from "../proc-probes.js";

// ── parseProcLines: PID, PPID, CMDLINE ───────────────────────────────────────────────────────
{
  const rows = parseProcLines("1234\t11\tnode x.js --flag\n5678\t22\tclaude.exe --resume h");
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0], { pid: 1234, ppid: 11, commandLine: "node x.js --flag" });
  assert.equal(rows[1].pid, 5678);
  assert.equal(rows[1].commandLine, "claude.exe --resume h");

  assert.deepEqual(parseProcLines(""), [], "no output is no rows, not a throw");
  assert.deepEqual(parseProcLines(undefined), [], "and neither is undefined");

  const messy = parseProcLines("\n\nbad line\n7\t8\tok\n");
  assert.deepEqual(messy, [{ pid: 7, ppid: 8, commandLine: "ok" }],
    "blank and malformed lines are dropped rather than becoming rows with NaN pids — a NaN pid is a "
    + "kill target that matches nothing, or worse, something");

  const tabsInCmdline = parseProcLines("9\t10\tnode a.js\targ");
  assert.equal(tabsInCmdline[0].commandLine, "node a.js\targ",
    "a tab INSIDE the command line survives — only the first two are field separators");
}

// ── cmdlineDeliveryLoopAgent: name the agent a delivery loop belongs to ──────────────────────
{
  assert.equal(
    cmdlineDeliveryLoopAgent("node /x/mcp/stdio/hermes-managed-host.js run sc-coder"),
    "sc-coder",
  );
  // A QUOTED SCRIPT PATH DOES NOT MATCH, and this pins the behaviour rather than blessing it. The
  // pattern needs whitespace between `hermes-managed-host.js` and `run`, so a command line of the
  // form `node "…\hermes-managed-host.js" run sc-coder` — a quote before the space — yields null and
  // the loop is not recognised as that agent's. Whether Windows process listings ever quote the
  // script path is an open question worth an operator's answer; the consequence if they do is an
  // orphaned delivery loop the env-scoped reaper walks past.
  assert.equal(
    cmdlineDeliveryLoopAgent('node "C:\\x\\mcp\\stdio\\hermes-managed-host.js" run sc-coder'),
    null,
    "documented, not endorsed — see the note above",
  );
  assert.equal(cmdlineDeliveryLoopAgent("node /x/mcp/stdio/hermes-managed-host.js"), null,
    "no `run <agent>` is no agent — never a guess");
  assert.equal(cmdlineDeliveryLoopAgent("node /x/mcp/stdio/hermes-managed-host.js ensure-host"), null,
    "`ensure-host` is not a long-lived delivery loop and must not be reaped as one");
  assert.equal(cmdlineDeliveryLoopAgent(""), null, "the miss value is null, not the empty string");
  assert.equal(cmdlineDeliveryLoopAgent(undefined), null);
}

// ── cmdlineResidentAgent: the same question for a resident wrapper ───────────────────────────
{
  assert.equal(cmdlineResidentAgent("hermes-aify --aify-agent sc-coder"), "sc-coder");
  assert.equal(cmdlineResidentAgent("hermes-aify --aify-agent=sc-coder"), "sc-coder",
    "both `=` and whitespace separate the flag from its value");
  // THE MUTUAL EXCLUSION IS THE LOAD-BEARING PART. A managed delivery loop's command line also
  // carries `--aify-agent`, so without this guard the same process would be classified as BOTH a
  // delivery loop and a resident wrapper — and reaped by whichever sweep looked first, under rules
  // written for the other kind of process.
  assert.equal(
    cmdlineResidentAgent("node /x/mcp/stdio/hermes-managed-host.js run sc-coder --aify-agent sc-coder"),
    null,
    "a managed loop is never resident, even when it carries the resident flag",
  );
  assert.equal(cmdlineResidentAgent("hermes-aify"), null, "no flag is no agent");
  assert.equal(cmdlineResidentAgent(""), null);
  assert.equal(cmdlineResidentAgent(undefined), null);
}

// ── defaultListProcesses: injectable spawn, and a failure is an empty list ───────────────────
{
  // TWO PARSERS, ONE PER PLATFORM, and the test has to follow the branch it is running on.
  // On win32 it asks PowerShell for backtick-t separated fields and hands them to `parseProcLines`;
  // elsewhere it runs `ps -eo pid=,ppid=,args=` and parses SPACE-separated columns with its own
  // regex. Feeding either format to the other silently yields ZERO rows — which a reaper reads as
  // "nothing to reap", the failure that looks like success.
  const onWindows = process.platform === "win32";
  const calls = [];
  const fakeSpawn = (cmd, args, opts) => {
    calls.push({ cmd, args, opts });
    return { status: 0, stdout: onWindows ? "1\t2\tnode a.js\n" : "1 2 node a.js\n" };
  };
  const rows = defaultListProcesses(fakeSpawn);
  assert.equal(calls.length, 1, "it shells out exactly once");
  assert.equal(calls[0].cmd, onWindows ? "powershell.exe" : "ps");
  assert.deepEqual(rows, [{ pid: 1, ppid: 2, commandLine: "node a.js" }]);

  // The WRONG format for this platform is zero rows, not a throw and not garbage — pinned because
  // it is the shape a silent reaper failure takes.
  const wrongFormat = () => ({ status: 0, stdout: onWindows ? "1 2 node a.js\n" : "1\t2\tnode a.js\n" });
  assert.deepEqual(defaultListProcesses(wrongFormat), []);

  // A reaper whose enumeration THROWS takes the bridge down with it; one that sees an empty list
  // reaps nothing this cycle and tries again. The second is the recoverable failure.
  const throwing = () => { throw new Error("spawn ENOENT"); };
  assert.doesNotThrow(() => defaultListProcesses(throwing));
  assert.deepEqual(defaultListProcesses(throwing), []);

  const failed = () => ({ status: 1, stdout: "", stderr: "nope" });
  assert.deepEqual(defaultListProcesses(failed), [], "a non-zero exit is no processes, not garbage");
}

console.log("proc-probes.test.js: all assertions passed");
