#!/usr/bin/env node
// Tests for the host-reported dead-PTY detection + reporting (WS4 Task 4.2).
// The owning env bridge detects an `attached` console row whose local pid is
// dead and reports it; liveness + the reporter are injected so no real process
// or HTTP call is touched.

import assert from "node:assert/strict";
import { test } from "node:test";
import { findDeadOwnedSessions, reportDeadOwnedSessions } from "../dead-pty-reporter.js";

test("findDeadOwnedSessions: only attached rows with a dead pid are returned", () => {
  const owned = [
    { terminalId: "t-alive", pid: 100, status: "attached", agentId: "a" },
    { terminalId: "t-dead", pid: 200, status: "attached", agentId: "b" },
    { terminalId: "t-stopping", pid: 300, status: "stopping", agentId: "c" },
    { terminalId: "t-stopped-dead", pid: 400, status: "stopped", agentId: "d" },
  ];
  const isAlive = (pid) => pid === 100; // only 100 is alive
  const dead = findDeadOwnedSessions(owned, { isAlive });
  assert.deepEqual(dead.map((s) => s.terminalId), ["t-dead"]);
});

test("findDeadOwnedSessions: ignores rows without a valid pid", () => {
  const owned = [
    { terminalId: "t-nopid", pid: 0, status: "attached" },
    { terminalId: "t-nan", pid: "x", status: "attached" },
  ];
  const dead = findDeadOwnedSessions(owned, { isAlive: () => false });
  assert.deepEqual(dead, []);
});

test("reportDeadOwnedSessions: reports each dead row with terminalId + pid", async () => {
  const owned = [
    { terminalId: "t-alive", pid: 100, status: "attached", agentId: "a" },
    { terminalId: "t-dead", pid: 200, status: "attached", agentId: "b" },
  ];
  const reports = [];
  const reported = await reportDeadOwnedSessions(owned, {
    isAlive: (pid) => pid === 100,
    report: async (r) => reports.push(r),
  });
  assert.deepEqual(reported, ["t-dead"]);
  assert.equal(reports.length, 1);
  assert.equal(reports[0].terminalId, "t-dead");
  assert.equal(reports[0].pid, 200);
  assert.equal(reports[0].agentId, "b");
});

test("reportDeadOwnedSessions: a failing report does not abort the rest and never throws", async () => {
  const owned = [
    { terminalId: "t-dead-1", pid: 201, status: "attached" },
    { terminalId: "t-dead-2", pid: 202, status: "attached" },
  ];
  const reported = await reportDeadOwnedSessions(owned, {
    isAlive: () => false,
    report: async (r) => {
      if (r.terminalId === "t-dead-1") throw new Error("boom");
      return undefined;
    },
  });
  assert.deepEqual(reported, ["t-dead-2"], "failed report skipped, remaining still reported");
});

test("reportDeadOwnedSessions: no-op without a report fn (never kills anything)", async () => {
  const reported = await reportDeadOwnedSessions([{ terminalId: "x", pid: 1, status: "attached" }], {
    isAlive: () => false,
  });
  assert.deepEqual(reported, []);
});

test("findDeadOwnedSessions: alive pid is never reported", () => {
  const owned = [{ terminalId: "t", pid: 999, status: "attached" }];
  const dead = findDeadOwnedSessions(owned, { isAlive: () => true });
  assert.deepEqual(dead, []);
});
