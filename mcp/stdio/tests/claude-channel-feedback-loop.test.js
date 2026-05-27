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

// Case A: hasActiveRun=true → re-pulse with that run's id.
{
  const d = decideRepulse({
    status: "working",
    dispatchState: {
      hasActiveRun: true,
      activeRun: { runId: "run_abc123" },
    },
  });
  assert.equal(d.repulse, true);
  assert.equal(d.runId, "run_abc123");
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

// Case E: hasActiveRun=true but activeRun missing → re-pulse with empty id (still acceptable; runId may not yet be hydrated by GET).
{
  const d = decideRepulse({
    status: "working",
    dispatchState: { hasActiveRun: true },
  });
  assert.equal(d.repulse, true);
  assert.equal(d.runId, "");
}

console.log("claude-channel-feedback-loop.test.js: all assertions passed");
