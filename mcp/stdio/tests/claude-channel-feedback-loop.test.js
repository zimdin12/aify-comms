#!/usr/bin/env node
// Regression: claude-channel.js's LAST_DELIVERED-driven re-pulse must
// only fire when there's an actual unsettled dispatch run, NOT just
// because the server-derived status is 'working'. The latter would
// create a self-reinforcing feedback loop because the server derives
// status='working' from turn_busy=1, so any re-pulse based on server
// status would re-arm turn_busy → status stays working → ...
//
// Operator-reported 2026-05-23: "your and sc-coder status were stuck
// at working" — comms-tech-lead's turn_busy was being kept fresh by
// this loop for 10+ minutes after every dispatch even when the agent
// had genuinely finished and Stop hook had fired.

import assert from "node:assert/strict";
import { decideRepulse } from "../claude-channel.js";

// Case A: hasActiveRun=true with an IN-FLIGHT run (running) → re-pulse with that run's id.
{
  const d = decideRepulse({
    status: "working",
    dispatchState: {
      hasActiveRun: true,
      activeRun: { runId: "run_abc123", status: "running" },
    },
  });
  assert.equal(d.repulse, true);
  assert.equal(d.runId, "run_abc123");
}

// Case A2: in-flight run with status 'claimed' → re-pulse.
{
  const d = decideRepulse({
    status: "working",
    dispatchState: {
      hasActiveRun: true,
      activeRun: { runId: "run_claimed", status: "claimed" },
    },
  });
  assert.equal(d.repulse, true, "claimed run is in-flight → re-pulse");
  assert.equal(d.runId, "run_claimed");
}

// Case A3: DELIVERED require_reply run (agent finished, merely owes a reply).
// MUST NOT re-pulse — re-pulsing turn_busy keeps the server's turn_busy branch
// lighting up "working" instead of the intended idle "online / awaiting reply"
// state (shared-status bug, operator-reported 2026-06-01).
{
  const d = decideRepulse({
    status: "working",
    dispatchState: {
      hasActiveRun: true,
      activeRun: { runId: "run_delivered", status: "delivered" },
    },
  });
  assert.equal(d.repulse, false, "delivered-awaiting-reply must NOT re-pulse turn_busy");
  assert.equal(d.runId, "");
}

// Case B: hasActiveRun=false BUT status='working' (the bug scenario:
// turn_busy=1 stale-but-fresh from a previous delivery whose run has
// already completed; server-derived status pretends 'working').
// MUST return repulse=false — otherwise the feedback loop reignites.
{
  const d = decideRepulse({
    status: "working",
    dispatchState: { hasActiveRun: false, activeRun: null },
  });
  assert.equal(d.repulse, false, "must NOT re-pulse on derived status='working' alone");
  assert.equal(d.runId, "");
}

// Case C: explicitly idle.
{
  const d = decideRepulse({
    status: "online",
    dispatchState: { hasActiveRun: false },
  });
  assert.equal(d.repulse, false);
}

// Case D: missing dispatchState → safe default (no re-pulse).
{
  const d = decideRepulse({});
  assert.equal(d.repulse, false);
  assert.equal(d.runId, "");
}

// Case E: hasActiveRun=true but activeRun missing / status not resolvable →
// MUST NOT re-pulse. Without a status we cannot prove the run is in-flight, and
// the safe failure mode is the idle "awaiting reply" state (the old behaviour of
// re-pulsing here is exactly what pinned idle delivered-reply agents to working).
{
  const d = decideRepulse({
    status: "working",
    dispatchState: { hasActiveRun: true },
  });
  assert.equal(d.repulse, false, "no resolvable in-flight status → no re-pulse");
  assert.equal(d.runId, "");
}

console.log("claude-channel-feedback-loop.test.js: all assertions passed");
