// Claude hook-independent turn-STATE detector loop (pure-event-status change #1,
// 2026-06-02; rewritten 2026-06-02 to a STRUCTURAL signal; made BIDIRECTIONAL
// 2026-06-02).
//
// Wraps the pure makeTurnEndDetector in a periodic loop: every `intervalMs` it
// reads a STRUCTURAL summary of the claude transcript TAIL (readTranscript →
// adapter.transcriptTail: { lastRole, lastStopReason, pendingToolUse }) and acts
// on the detector's edge-triggered directive:
//   "start" -> POST /turn-start (SET working). This is the RESIDENT under-report
//      fix: a channel-woken / scheduled turn never fires UserPromptSubmit→
//      /turn-start, so without this the agent shows NOT working while it is. The
//      detector reads the transcript, so it covers typed, channel-woken, AND
//      scheduled turns — the robust replacement for the removed PostToolUse
//      re-pulse.
//   "end"   -> POST /turn-end (CLEAR). The backstop for a MISSED claude Stop hook
//      (interrupt/ESC, MCP-continuation, crash, or a failed curl) — the cause of
//      the sc-claude "stuck at turn_busy=1" symptom.
// The fast-path hooks (UserPromptSubmit→/turn-start, Stop→/turn-end) stay the
// instant path; this is the hook-independent backstop that now covers BOTH
// directions (≤ intervalMs latency to reflect working, which is acceptable).
//
// WHY STRUCTURAL (the ship-blocker the rewrite fixes): the previous loop read
// transcript GROWTH and fired after one no-growth tick. But the parent transcript
// is STATIC during a long blocking tool call, a long generation, or a Task
// sub-agent dispatch (sub-agents write a SEPARATE subagents/*.jsonl), so it
// false-cleared turn_busy mid-turn. Reading tail STRUCTURE keeps the agent
// `working` through all of those and only fires on real transitions.
//
// ANTI-FEEDBACK-LOOP INVARIANT: this loop only ever READS transcript STRUCTURE
// (process truth) and POSTs the existing /turn-start and /turn-end endpoints. It
// NEVER reads the server's computed status, so it cannot re-arm a derived status
// into a busy signal. A null/unreadable tail yields no directive (never
// false-clear, never false-set).
//
// File budget per 500-line rule: tiny by design.

import { makeTurnEndDetector } from "./turn-end-detector.js";

export function startClaudeTurnEndDetector({ intervalMs, readTranscript, postTurnStart, postTurnEnd }) {
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
    let directive = null;
    try { directive = detector.observe(curr); } catch { return; }
    if (!directive || stopped) return;
    if (directive === "start") {
      // Best-effort; back-compat: a caller that only wires the clear path simply
      // skips the set. The instant UserPromptSubmit hook covers typed turns.
      if (typeof postTurnStart === "function") {
        try { await postTurnStart(); } catch { /* best-effort; next transition retries */ }
      }
      return;
    }
    // directive === "end"
    try { await postTurnEnd(); } catch { /* best-effort; the long ceiling still self-heals */ }
  };

  const timer = setInterval(tick, intervalMs);
  if (typeof timer.unref === "function") timer.unref();

  return () => {
    stopped = true;
    clearInterval(timer);
  };
}
