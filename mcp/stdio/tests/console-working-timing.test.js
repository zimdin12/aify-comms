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
import { TerminalProcessManager } from "../terminal-runtime.js";
import { TURN_BUSY_HEARTBEAT_MS } from "../turn-busy-heartbeat.js";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const LIVENESS_PY = path.join(REPO, "service", "api_core", "liveness.py");
const LIVE_PROBES_PY = path.join(REPO, "service", "api_core", "live_process_probes.py");

/** Read an integer constant from the service module that owns it. */
function pythonInt(name, file = LIVENESS_PY) {
  const source = readFileSync(file, "utf-8");
  const line = source.split("\n").find((l) => l.startsWith(`${name} = `));
  assert.ok(line, `${name} not found in ${path.basename(file)} — moved or renamed?`);
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

test("the idle re-probe re-detects a quiet turn before the service lease lapses", () => {
  // THE PATH THIS FILE DID NOT COVER. The pulse assertions above govern a turn that is actively
  // emitting. This one governs the other half of #224: a managed claude whose console has looked idle
  // for two minutes drops to a slow re-probe, and if that re-probe is slower than the lease, a turn
  // that resumed quietly is never re-detected -- the lease lapses and status flips working -> online
  // while the agent is still working. That is the symptom, on a bridge that contains both of #224's
  // fixes, reported live on 2026-08-25.
  //
  // Constructed with NO overrides on purpose: the defaults are the values that ship, and a test that
  // passed its own numbers in would assert nothing about them.
  const manager = new TerminalProcessManager({ onOutput: async () => {} });
  const reprobeMs = manager.consoleKeepaliveMs * manager.consoleKeepaliveIdleReprobeTicks;
  const leaseMs = pythonInt("CONSOLE_WORKING_LEASE_SECONDS") * 1000;

  assert.ok(
    Number.isFinite(reprobeMs) && reprobeMs > 0,
    `the re-probe interval read as ${reprobeMs}ms, so the comparison below proves nothing`,
  );
  assert.ok(
    reprobeMs < leaseMs,
    `the idle re-probe fires every ${reprobeMs}ms and the service lease lasts ${leaseMs}ms — a turn `
      + "that resumes quietly is re-detected only AFTER the lease has lapsed, which is #224 exactly",
  );

  // NOT asserted at the two-intervals-of-headroom bar the pulse path is held to, and that is a
  // finding rather than an oversight: 16000ms against a 20000ms lease is 1.25x, where the test above
  // demands 2x and justifies it as "the normal case on a busy host rather than an exceptional one".
  // The re-probe path needs MORE headroom than the pulse path, not less — a nudge has to reach the
  // PTY, the console has to repaint, and only then does a pulse POST, all inside the lease. Tightening
  // it is a tuning change to a live status path and belongs to whoever can validate it against a real
  // agent; this assertion pins the direction so nobody widens the gap by accident in the meantime.
});

test("the mid-turn heartbeat refreshes the bridge lease well inside the server's stale window", () => {
  // A SECOND CROSS-LANGUAGE PAIR, found by censusing which service constants the bridge names in
  // prose. While a native controller is mid-turn, server.js beats /turn-start + /heartbeat every
  // TURN_BUSY_HEARTBEAT_MS to keep bridge_instances.last_seen fresh. The server reaps an active run
  // whose owning bridge has been quiet for ACTIVE_RUN_BRIDGE_STALE_SECONDS. If the beat ever grows
  // past that window — or the window shrinks below the beat — a tool call simply longer than the
  // window gets its live run reaped as a dead bridge, mid-turn. That is not hypothetical: the comment
  // beside the call site records it happening, which is why the heartbeat exists at all.
  //
  // The interval used to be a literal inside server.js, where nothing could reach it: importing the
  // bridge entrypoint to read a number would start a bridge. It is now a named export on the pure
  // module, which is what makes this assertion possible at all.
  const staleMs = pythonInt("ACTIVE_RUN_BRIDGE_STALE_SECONDS", LIVE_PROBES_PY) * 1000;

  assert.ok(
    TURN_BUSY_HEARTBEAT_MS < staleMs,
    `the bridge beats every ${TURN_BUSY_HEARTBEAT_MS}ms and the server reaps an active run after `
      + `${staleMs}ms of bridge silence — a turn longer than that window is reaped while it is alive`,
  );
  assert.ok(
    staleMs >= TURN_BUSY_HEARTBEAT_MS * 2,
    `${staleMs}ms of stale window against a ${TURN_BUSY_HEARTBEAT_MS}ms beat leaves no room for one `
      + "dropped or slow POST, which on a loaded host is the normal case rather than an exceptional one",
  );
});
