// Claude hook-independent turn-END detector (pure-event-status change #1,
// 2026-06-02).
//
// WHY: the claude Stop hook (install.sh -> POST /turn-end) is NOT a guaranteed
// turn terminator. It misses on interrupt/ESC, MCP-continuations, a crash, or
// when its short-timeout curl fails. When it misses, the agent stays
// turn_busy=1 with no event to clear it — the observed sc-claude "stuck at
// turn_busy=1" symptom. With STATUS now pure-event (change #3 drops the short
// status window), a missed Stop hook would leave the agent `working` until the
// single long ceiling. This detector gives claude an EVENT-DRIVEN turn-end that
// does not depend on the Stop hook firing, by watching the transcript directly.
//
// HOW: the transcript .jsonl grows on every token + tool result, so GROWTH is
// proof claude is mid-turn (process truth). When a transcript that WAS growing
// (a turn was in flight) stops growing for one tick, the turn has ended -> fire
// /turn-end once. Re-arm on the next growth so the next turn is detected too.
// The Stop hook stays as the fast-path clear; this is the backstop for when it
// misses.
//
// ANTI-FEEDBACK-LOOP INVARIANT: this detector keys ONLY on transcript GROWTH
// (process truth), NEVER on the server's computed status — so it can never
// self-reinforce a derived status into turn_busy. It only ever CLEARS (fires
// turn-end); it never sets turn_busy.
//
// CONSERVATIVE BY DESIGN (false-clear safety): it fires turn-end ONLY after an
// observed growth phase is followed by a no-growth tick. A between-tool-calls
// pause that still produces growth (new tokens / tool results) keeps the turn
// "in flight" and never fires. Only sustained no-growth (one full ~30s tick
// with zero new bytes / no newer mtime) is read as turn-end, and any subsequent
// growth re-arms the detector — so a mid-turn agent that resumes writing is
// correctly seen as working again.

import { transcriptIsGenerating } from "./transcript-activity.js";

export function makeTurnEndDetector() {
  let prev = null;
  // True once we have observed growth (a turn is in flight) and have NOT yet
  // fired turn-end for it. Gates the single fire + the re-arm.
  let turnInFlight = false;

  return {
    // observe(curr): feed one transcript observation ({ mtimeMs, size } | null).
    // Returns true exactly on the tick that should POST /turn-end, false
    // otherwise. Idempotent across repeated no-growth ticks (fires at most once
    // per growth phase).
    observe(curr) {
      // Unreadable / sentinel observation (transient stat failure or
      // unresolved session id): ignore entirely. Do NOT advance the baseline
      // and do NOT treat it as no-growth — a failed read is not evidence the
      // turn ended (false-clear safety).
      if (!curr || !curr.mtimeMs) return false;
      const growing = transcriptIsGenerating(prev, curr);
      prev = curr;
      if (growing) {
        // Mid-turn (or turn (re)started): arm and never fire on a growth tick.
        turnInFlight = true;
        return false;
      }
      // No growth this tick. Fire turn-end iff a turn was in flight; then
      // disarm so we do not re-fire until the next growth re-arms us.
      if (turnInFlight) {
        turnInFlight = false;
        return true;
      }
      return false;
    },
  };
}
