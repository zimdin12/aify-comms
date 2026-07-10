#!/usr/bin/env node
import assert from "node:assert/strict";
import { terminalControlFailurePatch, orphanPidReapAllowed } from "../terminal-control.js";

// ── orphanPidReapAllowed: the kill-by-pid identity guard (2026-07-10 bughunt HIGH) ──
// Refuse ONLY when the pid's cmdline positively names a DIFFERENT agent; fail OPEN
// on every uncertainty so a legitimate orphan Stop is never silently dropped.
const cmdOf = (map) => (pid) => map[pid] || "";

// Positively a different agent (recycled pid → sibling worker) → REFUSE.
assert.equal(
  orphanPidReapAllowed(4321, { agentId: "sc-coder" }, {
    getCmdline: cmdOf({ 4321: "cmd.exe /c claude-aify --aify-agent sc-architect --resume x" }),
  }),
  false,
  "a pid whose cmdline names a DIFFERENT agent must not be reaped (recycled pid)",
);

// Same agent → ALLOW (the real orphan).
assert.equal(
  orphanPidReapAllowed(4321, { agentId: "sc-coder" }, {
    getCmdline: cmdOf({ 4321: "cmd.exe /c claude-aify --aify-agent sc-coder --resume x" }),
  }),
  true,
  "a pid whose cmdline names THIS agent is the real orphan → reap",
);

// No --aify-agent marker in cmdline → fail OPEN (don't drop a legit Stop).
assert.equal(
  orphanPidReapAllowed(4321, { agentId: "sc-coder" }, { getCmdline: cmdOf({ 4321: "some-random.exe --flag" }) }),
  true,
  "no --aify-agent marker → cannot prove a different agent → allow (fail-open)",
);

// Unreadable / empty cmdline → fail OPEN.
assert.equal(
  orphanPidReapAllowed(4321, { agentId: "sc-coder" }, { getCmdline: () => { throw new Error("cim fail"); } }),
  true,
  "an unreadable cmdline must fail open (never drop a Stop)",
);
assert.equal(
  orphanPidReapAllowed(4321, { agentId: "sc-coder" }, { getCmdline: () => "" }),
  true,
  "an empty cmdline must fail open",
);

// No control agentId (resident console) or no getCmdline → current behavior (allow).
assert.equal(orphanPidReapAllowed(4321, { agentId: "" }, { getCmdline: cmdOf({}) }), true, "no agentId → allow");
assert.equal(orphanPidReapAllowed(4321, { agentId: "sc-coder" }, {}), true, "no getCmdline → allow");

// Invalid pid → false (nothing to reap), matching orphanPidToKill's own pid guard.
assert.equal(orphanPidReapAllowed(0, { agentId: "sc-coder" }, {}), false, "pid 0 → not reapable");
assert.equal(orphanPidReapAllowed("x", { agentId: "sc-coder" }, {}), false, "non-numeric pid → not reapable");

// A longer agent id that merely CONTAINS the target must not be confused (word boundary).
assert.equal(
  orphanPidReapAllowed(4321, { agentId: "sc-coder" }, {
    getCmdline: cmdOf({ 4321: "cmd.exe /c claude-aify --aify-agent sc-coder-2 --resume x" }),
  }),
  false,
  "sc-coder-2 is a DIFFERENT agent than sc-coder → refuse",
);

assert.deepEqual(
  terminalControlFailurePatch("start", new Error('spawn "omp" ENOENT')),
  { status: "failed", terminalStatus: "failed", error: 'spawn "omp" ENOENT' },
  "start failures should mark the terminal failed",
);

assert.deepEqual(
  terminalControlFailurePatch("input", new Error('Terminal "term-1" is not running')),
  { status: "failed", terminalStatus: "stopped", error: 'Terminal "term-1" is not running' },
  "late input failures after terminal exit should preserve stopped terminal state",
);

assert.deepEqual(
  terminalControlFailurePatch("resize", new Error('Terminal "term-1" is not running')),
  { status: "failed", terminalStatus: "stopped", error: 'Terminal "term-1" is not running' },
  "late resize failures after terminal exit should preserve stopped terminal state",
);

assert.deepEqual(
  terminalControlFailurePatch("stop", new Error('Terminal "term-1" is not running')),
  { status: "failed", terminalStatus: "stopped", error: 'Terminal "term-1" is not running' },
  "late stop failures after terminal exit should preserve stopped terminal state",
);

console.log("terminal-control.test.js: all assertions passed");
