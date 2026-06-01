// Growth-based transcript activity (status-liveness fix, 2026-06-01).
//
// Background: claude's turn-busy heartbeat used "transcript mtime within the
// last N seconds" as a proxy for "claude is mid-turn". That FRESHNESS signal is
// wrong at turn end: the claude Stop hook clears turn_busy server-side AND
// writes the final assistant message to the transcript (mtime fresh). The very
// next heartbeat tick then sees a "fresh" mtime and re-asserts turn_busy=1,
// defeating the Stop-hook clear. Because mere freshness (not growth) was the
// signal, ticks kept re-pulsing while mtime stayed inside the window, so an
// idle resident showed `working` for up to ~150s after every turn.
//
// Fix: count the transcript as "active" only when it has GROWN since the last
// observation (ongoing generation) — not merely been touched recently. During
// streaming, consecutive ticks see growth → active. After the final write, AT
// MOST ONE tick sees that growth; the following tick sees no further growth →
// false, so turn_busy is allowed to stay cleared.

export function transcriptIsGenerating(prev, curr) {
  // prev/curr: { mtimeMs, size } | null. Active iff the transcript advanced
  // since the last observation (new bytes / newer mtime) — i.e. ongoing
  // generation, NOT a single stale write lingering inside a freshness window.
  if (!curr || !curr.mtimeMs) return false;
  if (!prev) return false; // first observation establishes a baseline, not active
  return (
    curr.mtimeMs > prev.mtimeMs ||
    (curr.size != null && prev.size != null && curr.size > prev.size)
  );
}
