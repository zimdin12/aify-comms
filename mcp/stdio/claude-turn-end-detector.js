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

import { makeTurnEndDetector, classify } from "./turn-end-detector.js";

export function startClaudeTurnEndDetector({ intervalMs, readTranscript, postTurnStart, postTurnEnd, workingRefreshMs = 45000, idleRefreshMs = 45000 }) {
  const noop = () => {};
  if (typeof readTranscript !== "function" || typeof postTurnEnd !== "function"
      || !Number.isFinite(intervalMs) || intervalMs <= 0) {
    return noop;
  }
  let stopped = false;
  const detector = makeTurnEndDetector();
  // KEEP-FRESH (2026-06-12): /turn-start is edge-triggered, but the SERVER can clear
  // turn_busy MID-TURN — the delivery-completion send-deadlock clear fires when a
  // steered/queued message lands and no reply-owing run remains
  // (_clear_turn_busy_if_no_open_reply_owing_run). After that an edge-triggered
  // detector never re-fires, so a hard-working resident read `online` until its next
  // turn boundary (operator-reported, 2026-06-12: comms-tech-lead online mid-
  // investigation the moment a dashboard message steered in). While the transcript
  // stays IN-FLIGHT, re-stamp /turn-start every workingRefreshMs so any server-side
  // clear heals within one window. Mirrors hermes-gateway-turn-detector's re-stamp.
  // 0 disables (edge-only back-compat).
  const refreshMs = Math.max(0, Number(workingRefreshMs) || 0);
  // KEEP-CLEARED (symmetric mirror of KEEP-FRESH, 2026-07-13): the SET direction re-asserts
  // /turn-start from proof every refreshMs so a spurious server clear self-heals. The CLEAR
  // direction was edge-ONLY, so a stray in_turn set OUTSIDE this detector's view — a hook /
  // channel-sidecar /turn-start whose end-event was lost, which this edge-triggered clear never
  // fired for — latched `working` until the 30-min ceiling (operator: general-manager stuck
  // working infinitely). Mirror it: while the transcript PROVES ended, re-assert /turn-end every
  // idleRefreshMs. Proof-driven (never time-decay of status): fires ONLY on classify "ended",
  // never on a null/unknown tail, and never while in-flight. 0 disables (edge-only back-compat).
  const clearMs = Math.max(0, Number(idleRefreshMs) || 0);
  let inFlight = false;
  let sinceRefresh = 0;
  let sinceClear = 0;

  const tick = async () => {
    if (stopped) return;
    let curr;
    try { curr = await readTranscript(); } catch { return; }
    let directive = null;
    try { directive = detector.observe(curr); } catch { return; }
    if (stopped) return;
    if (directive === "start") {
      inFlight = true;
      sinceRefresh = 0;
      sinceClear = 0;
      // Best-effort; back-compat: a caller that only wires the clear path simply
      // skips the set. The instant UserPromptSubmit hook covers typed turns.
      if (typeof postTurnStart === "function") {
        try { await postTurnStart(); } catch { /* best-effort; next transition retries */ }
      }
      return;
    }
    if (directive === "end") {
      inFlight = false;
      sinceRefresh = 0;
      sinceClear = 0;
      try { await postTurnEnd(); } catch { /* best-effort; the long ceiling still self-heals */ }
      return;
    }
    // No directive: steady state.
    if (inFlight) {
      // KEEP-FRESH: re-stamp /turn-start while the transcript stays in-flight (see above).
      if (refreshMs > 0 && typeof postTurnStart === "function") {
        sinceRefresh += intervalMs;
        if (sinceRefresh >= refreshMs) {
          sinceRefresh = 0;
          try { await postTurnStart(); } catch { /* best-effort; next window retries */ }
        }
      }
    } else if (clearMs > 0 && classify(curr) === "ended") {
      // KEEP-CLEARED: the transcript PROVES the turn ended. Re-assert /turn-end so a stray
      // in_turn from a source this edge-detector never saw start clears within one window,
      // not the 30-min ceiling. Gated on classify==="ended" (proven idle) — a null/unknown
      // tail never false-clears; /turn-end is idempotent and can never re-arm working.
      sinceClear += intervalMs;
      if (sinceClear >= clearMs) {
        sinceClear = 0;
        try { await postTurnEnd(); } catch { /* best-effort; next window retries */ }
      }
    }
  };

  const timer = setInterval(tick, intervalMs);
  if (typeof timer.unref === "function") timer.unref();

  return () => {
    stopped = true;
    clearInterval(timer);
  };
}
