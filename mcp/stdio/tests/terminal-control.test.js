#!/usr/bin/env node
import assert from "node:assert/strict";
import { terminalControlFailurePatch } from "../terminal-control.js";

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
