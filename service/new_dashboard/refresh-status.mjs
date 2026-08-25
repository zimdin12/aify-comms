// What the connection chip says after a poll cycle.
//
// TWO REAL CONCERNS, and the original code served one of them.
//
// It painted the chip green whenever /agents succeeded, whatever else had failed, with the reasoning
// recorded in refresh-cycle.test.mjs: "Deliberate asymmetry: agents are the core slice.
// Stats/settings blipping is noise." That is right, and it is not a small point — a chip that flickers
// amber on every transient blip is a chip the operator learns to ignore, which costs more than it
// saves. It dates from the era when the single-worker service dropped requests under poll load, which
// is also why the poll uses allSettled and keeps each slice's last-good value.
//
// What it missed is the other end. Nine of ten slices failing is not noise, and it produced exactly
// the same green. The operator then reads message lists, spawn requests, sessions, stats and settings
// that are arbitrarily old while the chip says the view is current — and staleness has no other tell,
// because a slice that kept its last-good value renders identically to one where nothing changed.
//
// So the rule is PERSISTENCE, not presence. A slice that failed on this cycle alone is a blip and
// stays green. A slice that failed on this cycle AND the one before it is stale, and is named. That
// encodes both concerns instead of choosing between them, and it costs one cycle of delay (~15s)
// before a real outage shows — which is the price of not crying wolf.
//
// MEASURED, 2026-08-25, before changing anything: 120 requests across 15 cycles against the live
// service produced 0 non-200 responses. Blips are not currently common. That is one quiet sample and
// not proof they never happen, which is precisely why the blip tolerance stays.
//
// Pure, and returns a description rather than touching the DOM, so the decision is tested by calling
// it rather than by booting a dashboard.

/**
 * The slices of one refresh cycle, in the order they are fetched. Named here so the chip can say
 * WHICH data is stale rather than only how much — a bare count starts a hunt, and the hunt is the
 * expensive part.
 */
export const REFRESH_SLICES = Object.freeze([
  'agents', 'contracts', 'inbox', 'messages', 'runs',
  'sessions', 'environments', 'spawn requests', 'stats', 'settings',
]);

/** The index whose failure means no data is current, rather than that some of it is old. */
export const AGENTS_SLICE = 0;

// FETCHES THAT ARE NOT IN THE allSettled ARRAY.
//
// The cycle issues more requests than the ten it settles together. Observed on the running
// dashboard 2026-08-25 -- twelve to thirteen per cycle, not ten: the contracts re-filter, the
// channel list, an open conversation and the shared-files list are separate awaits, each wrapped in
// `try { ... } catch (_) {}` that swallows the failure entirely. So they could fail for ever while
// this chip said `live`, which is the exact defect the chip was rewritten to end -- one layer over,
// and only visible by watching the browser rather than reading the array.
//
// They cannot join `settled` without serialising differently, so they report themselves instead.
export const OUT_OF_BAND_SLICES = Object.freeze([
  'contract filter', 'channels', 'conversation', 'files',
]);

/** Failures reported by the out-of-band awaits since the last chip paint. */
let outOfBandFailures = new Set();

/**
 * Record that an out-of-band fetch failed this cycle.
 *
 * Called from the catch that already exists, so the swallow becomes a report rather than silence.
 * Unknown names are accepted: a caller inventing one is still better evidence than nothing, and the
 * test pairs this list against the call sites.
 */
export function noteSliceFailure(name) {
  const slice = String(name || '').trim();
  if (slice) outOfBandFailures.add(slice);
}

// ONE CYCLE OF MEMORY, owned here.
//
// It lives in this module rather than in `state` or in refresh-cycle's module scope, for two
// reasons. The module that decides what counts as stale is the one that needs the history, and it
// has exactly one reader — so this is state with an owner, not state at large. And `state` is
// reconstructed byte-for-byte from the pre-extraction app.js by extraction-proof.test.mjs, so a
// field added there is a change outside a declared span; the gate caught that on the first attempt.
let previousFailures = [];

/** Forget the history. For tests that need a defined starting point rather than the last one's. */
export function resetRefreshHistory() {
  previousFailures = [];
  outOfBandFailures = new Set();
}

/** Which slices this cycle failed to refresh. Exported so the caller can feed it back in. */
export function rejectedSlices(settled, names = REFRESH_SLICES) {
  const results = Array.isArray(settled) ? settled : [];
  const out = [];
  for (let i = 0; i < results.length; i += 1) {
    if (results[i]?.status === 'rejected') out.push(names[i] ?? `slice ${i}`);
  }
  return out;
}

/**
 * @param {Array<{status: string}>} settled Promise.allSettled results, in REFRESH_SLICES order
 * @param {{previouslyFailed?: readonly string[], names?: readonly string[]}} [options]
 *   previouslyFailed — what `rejectedSlices` returned last cycle. Omitted means "no history", and a
 *   first-ever cycle therefore reports no staleness: with one observation, blip and outage are the
 *   same picture, and guessing between them is what this module exists to stop.
 * @returns {{text: string, className: string, title: string, stale: string[], failed: string[]}}
 */
export function refreshChipState(settled, { previouslyFailed, names = REFRESH_SLICES } = {}) {
  // The caller may pin the history (tests do). Otherwise this module remembers, so no call site
  // has to thread it -- a guard every caller must remember to pass is a guard that stops guarding.
  const before = new Set(previouslyFailed ?? previousFailures);
  // DRAINED, once per paint. The out-of-band awaits run BEFORE the chip is painted, so whatever
  // they reported belongs to this cycle; carrying it into the next one would report a slice as
  // stale twice for a single failure and trip the two-cycle rule on its own.
  const failed = [...rejectedSlices(settled, names), ...outOfBandFailures];
  outOfBandFailures = new Set();
  const stale = failed.filter((name) => before.has(name));
  previousFailures = failed;

  // No data is current. Its own state, and NOT folded into staleness: "the service is unreachable"
  // and "six panels are a minute old" call for different reactions. Immediate, with no blip
  // tolerance — losing the roster is never noise, and `state.loaded` already turns on it.
  if (failed.includes(names[AGENTS_SLICE])) {
    return {
      text: 'reconnecting',
      // Unchanged from before this module existed. Amber, not red: it self-heals on the next cycle,
      // and escalating it was not the defect being fixed here.
      className: 'status-chip warn',
      title: failed.length > 1
        ? `Cannot reach the service. Not refreshed: ${failed.join(', ')}.`
        : 'Cannot reach the service.',
      stale: [],
      failed,
    };
  }

  if (!stale.length) {
    return {
      text: 'live',
      className: 'status-chip ok',
      // A blip is still worth saying in the tooltip, where it costs nobody anything, even though it
      // does not move the chip.
      title: failed.length
        ? `All data current. Retrying: ${failed.join(', ')}.`
        : 'All data refreshed.',
      stale: [],
      failed,
    };
  }

  // THE STATE THAT DID NOT EXIST. Amber, never green: some of what is on screen is older than the
  // rest, it has been for at least two cycles, and there is no way to tell by looking at it.
  return {
    text: `${stale.length} stale`,
    className: 'status-chip warn',
    title: `Showing the last good copy of: ${stale.join(', ')}. Everything else refreshed.`,
    stale,
    failed,
  };
}
