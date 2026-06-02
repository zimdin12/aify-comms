// Claude hook-independent turn-END detector loop (pure-event-status change #1,
// 2026-06-02; rewritten 2026-06-02 to a STRUCTURAL signal).
//
// Wraps the pure makeTurnEndDetector in a periodic loop: every `intervalMs` it
// reads a STRUCTURAL summary of the claude transcript TAIL (readTranscript →
// adapter.transcriptTail: { lastRole, lastStopReason, pendingToolUse }) and, when
// the detector decides the turn has ENDED (last assistant message yielded to the
// user — terminal stop_reason, no pending tool_use), POSTs /turn-end. This is the
// backstop for a MISSED claude Stop hook (interrupt/ESC, MCP-continuation, crash,
// or a failed curl) — the cause of the sc-claude "stuck at turn_busy=1" symptom.
// The Stop hook stays the fast-path clear; this only fires when the tail shows a
// completed yield and the hook didn't.
//
// WHY STRUCTURAL (the ship-blocker this rewrite fixes): the previous loop read
// transcript GROWTH and fired after one no-growth tick. But the parent transcript
// is STATIC during a long blocking tool call, a long generation, or a Task
// sub-agent dispatch (sub-agents write a SEPARATE subagents/*.jsonl), so it
// false-cleared turn_busy mid-turn. Reading tail STRUCTURE instead keeps the
// agent `working` through all of those and only fires on a real turn yield.
//
// ANTI-FEEDBACK-LOOP INVARIANT: this loop only ever READS transcript STRUCTURE
// (process truth) and only ever POSTs /turn-end (a CLEAR). It NEVER reads the
// server's computed status and NEVER sets turn_busy, so it cannot re-arm a
// derived status into a busy signal. A null/unreadable tail is treated as
// NOT-ended (never false-clear).
//
// File budget per 500-line rule: tiny by design.

import { makeTurnEndDetector } from "./turn-end-detector.js";

export function startClaudeTurnEndDetector({ intervalMs, readTranscript, postTurnEnd }) {
  const noop = () => {};
  if (typeof readTranscript !== "function" || typeof postTurnEnd !== "function"
      || !Number.isFinite(intervalMs) || intervalMs <= 0) {
    return noop;
  }
  let stopped = false;
  const detector = makeTurnEndDetector();

  const tick = async () => {
    if (stopped) return;
    let curr;
    try { curr = await readTranscript(); } catch { return; }
    let shouldEnd = false;
    try { shouldEnd = detector.observe(curr); } catch { return; }
    if (!shouldEnd || stopped) return;
    try { await postTurnEnd(); } catch { /* best-effort; the long ceiling still self-heals */ }
  };

  const timer = setInterval(tick, intervalMs);
  if (typeof timer.unref === "function") timer.unref();

  return () => {
    stopped = true;
    clearInterval(timer);
  };
}
