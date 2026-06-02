#!/usr/bin/env node
// Managed-hermes delivery-loop READY MARKER.
//
// The delivery loop (hermes-managed-host.js `run`) is the lifecycle owner of the
// managed-hermes triad. It writes this marker only AFTER it has become a LIVE
// CLAIMER — gateway ok + liveness heartbeat started + one successful
// /dispatch/claim round-trip — and refreshes it on every successful claim. The
// hermes-aify wrapper health-gates on this marker before exec'ing the visible
// TUI (Task 1.5): a TUI that can't receive work must never show (the visible-TUI
// HARD requirement). The loop clears the marker in teardown.
//
// Mirrors the per-agent pid-file helpers in hermes-daemon.js: a file under
// os.tmpdir() named `aify-hermes-loop-ready-<agent>`, freshness judged by mtime
// (so a stale/crash-leftover marker is not mistaken for a live loop), and a
// best-effort clear. File budget per the 500-line rule: tiny.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// Sanitize an agentId into a safe filename fragment. Kept identical to
// hermes-daemon.js / hermes-endpoint.js sanitizeAgentId so the ready marker sits
// next to the agent's port/key/daemon-pid files.
function sanitizeAgentId(agentId) {
  return String(agentId || "")
    .replace(/[^a-zA-Z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

// Absolute path to the ready marker for an agent.
export function loopReadyFile(agentId, dir, { fs: _fsImpl = fs } = {}) {
  return path.join(dir || os.tmpdir(), `aify-hermes-loop-ready-${sanitizeAgentId(agentId)}`);
}

// Write (or refresh) the ready marker for an agent. Writing always bumps the
// mtime, so calling it on each successful claim keeps the marker fresh.
// Best-effort; never throws. Returns true on success.
export function writeLoopReady(agentId, dir, { fs: fsImpl = fs } = {}) {
  if (!sanitizeAgentId(agentId)) return false;
  try {
    fsImpl.writeFileSync(loopReadyFile(agentId, dir), String(Date.now()));
    return true;
  } catch {
    return false;
  }
}

// Is the ready marker present AND fresh (mtime within maxAgeMs)? A missing or
// stale marker → false. Never throws.
export function loopReadyFresh(agentId, dir, maxAgeMs, { fs: fsImpl = fs } = {}) {
  if (!sanitizeAgentId(agentId)) return false;
  try {
    const stat = fsImpl.statSync(loopReadyFile(agentId, dir));
    const ageMs = Date.now() - stat.mtimeMs;
    return ageMs <= Math.max(0, Number(maxAgeMs) || 0);
  } catch {
    return false;
  }
}

// Remove the ready marker for an agent. Best-effort + idempotent; never throws.
// Returns true when the marker is known to be gone afterward.
export function clearLoopReady(agentId, dir, { fs: fsImpl = fs } = {}) {
  if (!sanitizeAgentId(agentId)) return false;
  const file = loopReadyFile(agentId, dir);
  try {
    fsImpl.rmSync(file, { force: true });
    return true;
  } catch {
    try {
      fsImpl.unlinkSync(file);
      return true;
    } catch {
      return false;
    }
  }
}
