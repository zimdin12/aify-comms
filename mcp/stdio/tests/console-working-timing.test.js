#!/usr/bin/env node
// The bridge PULSES and the service LEASES, and one has to outlast the other.
//
// While a managed claude console shows its working footer, the bridge posts `console-working` every
// CONSOLE_WORKING_REMIT_MS. The service treats that as proof of work for CONSOLE_WORKING_LEASE_SECONDS
// and ORs it into derived `working`. The two numbers are declared in different languages, in
// different repositories of knowledge, and neither mentions the other.
//
// IF THE LEASE IS SHORTER THAN THE PULSE INTERVAL the agent flickers: the lease expires, status drops
// out of `working`, the next pulse puts it back. That is the "online while thinking" under-report
// this whole mechanism was built to fix, re-created by a one-line edit to either side.
//
// The other two relations are internal to the bridge and equally silent when broken:
//   * a re-emit interval that is not SHORTER than the quiet window can never re-emit — the clear
//     timer fires first, and a long turn reports busy exactly once;
//   * an in-flight window that is not LONGER than the pulse interval can never be true between
//     pulses, so mid-turn "unknown" footer frames stop being bridged and a working agent reads idle.
//
// Both constants here were named by no test, which is what `every-export-is-named-by-a-test.test.js`
// surfaced. They are numbers whose ONLY meaning is their relation to another number — the case where
// a unit test of one value in isolation would assert nothing at all.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  CONSOLE_WORKING_REMIT_MS,
  CONSOLE_WORKING_TURN_WINDOW_MS,
  TERMINAL_TURN_BUSY_QUIET_MS,
  TERMINAL_TURN_BUSY_REMIT_MS,
} from "../terminal-manager.mjs";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const LIVENESS_PY = path.join(REPO, "service", "api_core", "liveness.py");

/** Read an integer constant from the service module that owns it. */
function pythonInt(name) {
  const source = readFileSync(LIVENESS_PY, "utf-8");
  const line = source.split("\n").find((l) => l.startsWith(`${name} = `));
  assert.ok(line, `${name} not found in service/api_core/liveness.py — moved or renamed?`);
  const value = Number(line.slice(line.indexOf("=") + 1).split("#")[0].trim());
  assert.ok(Number.isFinite(value) && value > 0, `${name} parsed as ${value}`);
  return value;
}

test("the parse reads a real number, not a default", () => {
  // Anti-vacuity: the comparison below is only meaningful if the service side was actually read.
  assert.equal(pythonInt("CONSOLE_WORKING_LEASE_SECONDS"), 20);
});

test("the service lease outlasts the bridge's pulse interval, with headroom", () => {
  const leaseMs = pythonInt("CONSOLE_WORKING_LEASE_SECONDS") * 1000;
  assert.ok(
    CONSOLE_WORKING_REMIT_MS < leaseMs,
    `the bridge pulses every ${CONSOLE_WORKING_REMIT_MS}ms and the service lease lasts ${leaseMs}ms — `
      + "the agent will flicker in and out of `working` while it is working",
  );
  assert.ok(
    leaseMs >= CONSOLE_WORKING_REMIT_MS * 2,
    "a lease under two pulse intervals leaves no room for one dropped or slow POST, which is the "
      + "normal case on a busy host rather than an exceptional one",
  );
});

test("the turn-busy re-emit interval is shorter than the quiet window", () => {
  assert.ok(
    TERMINAL_TURN_BUSY_REMIT_MS < TERMINAL_TURN_BUSY_QUIET_MS,
    `re-emit ${TERMINAL_TURN_BUSY_REMIT_MS}ms vs quiet ${TERMINAL_TURN_BUSY_QUIET_MS}ms — the clear `
      + "timer would fire before any re-emit, so a long autonomous turn reports busy exactly once",
  );
});

test("the in-flight window spans more than one pulse interval", () => {
  assert.ok(
    CONSOLE_WORKING_TURN_WINDOW_MS > CONSOLE_WORKING_REMIT_MS,
    `window ${CONSOLE_WORKING_TURN_WINDOW_MS}ms vs pulse ${CONSOLE_WORKING_REMIT_MS}ms — a turn `
      + "could never read as in flight BETWEEN pulses, which is when the bridging matters",
  );
});

test("every one of these is a positive whole number of milliseconds", () => {
  // They are handed to setTimeout and compared against Date.now() deltas. A zero, a float or a
  // stray string would not throw anywhere — it would quietly change the debounce into something
  // else, which is the failure mode of a timing constant nobody asserts.
  for (const [name, value] of [
    ["CONSOLE_WORKING_REMIT_MS", CONSOLE_WORKING_REMIT_MS],
    ["CONSOLE_WORKING_TURN_WINDOW_MS", CONSOLE_WORKING_TURN_WINDOW_MS],
    ["TERMINAL_TURN_BUSY_REMIT_MS", TERMINAL_TURN_BUSY_REMIT_MS],
    ["TERMINAL_TURN_BUSY_QUIET_MS", TERMINAL_TURN_BUSY_QUIET_MS],
  ]) {
    assert.equal(typeof value, "number", `${name} is not a number`);
    assert.ok(Number.isInteger(value) && value > 0, `${name} is ${value}`);
  }
});
