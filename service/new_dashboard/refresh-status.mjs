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
  // 'inbox' is BOTH: it keeps its slot in the settled array so ok(i)/val(i) still index by
  // position, and that slot now resolves null while the fetch itself happens after the settle, only
  // when /messages/recent did not hand us messages. A null slot is `fulfilled`, so the array can
  // never report this one -- the request has to speak for itself like the other four.
  'inbox',
]);

/**
 * When the service first became unreachable in the current outage, or null while it is reachable.
 *
 * WHY A TIMESTAMP AND NOT A COUNTER. `reconnecting` reads identically after five seconds and after
 * five hours, so the operator cannot tell a blip from an outage from the one indicator that is
 * supposed to tell them. That is the same gap an offline ENVIRONMENT card had -- `offline` with no
 * age -- and the same fix: say how long, and let the reader decide.
 *
 * THE COLOUR IS NOT TOUCHED. Whether amber should become red, and after how long, is a product
 * decision, and the branch below already records that escalating "was not the defect being fixed
 * here". This adds the missing FACT without taking that decision.
 */
let unreachableSince = null;

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
  // THE OUTAGE CLOCK IS HISTORY TOO, and leaving it out made this function a partial reset that
  // still looked total. A caller asking for "a defined starting point" would have got a brand-new
  // failure carrying the previous outage's age -- a wrong DURATION, which reads as evidence far more
  // readily than a missing one.
  unreachableSince = null;
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
/**
 * ` for 4m`, once an outage has been observed TWICE. Empty for a single miss.
 *
 * THE THRESHOLD IS A CYCLE, NOT A NUMBER OF SECONDS. A first version waited 30 seconds and called
 * that "one poll interval". It is not one: `app.js` derives the cadence from
 * `dashboard_refresh_seconds`, floored at 5 and exposed up to 300 in Settings, so a fixed 30s is six
 * intervals at the floor and a tenth of one at the ceiling. A duration on the first failed poll
 * would still be noise -- every transient miss reading as an outage of zero seconds -- but the
 * function already knows whether agents ALSO failed last cycle, and that is exactly one observed
 * interval whatever the cadence. At 5s it says `for 5s`; at 300s it says `for 5m`. No setting to
 * thread and no invented seconds.
 *
 * The clock still starts on the FIRST miss, so when the phrase does appear the age is the real one.
 */
function outageFor(now, sustained) {
  if (unreachableSince === null) unreachableSince = now;
  // ONE RULE, NOT TWO. A `seconds < 1` guard sat here as well, and it made the real rule
  // undetectable: on a first miss the clock is set to `now`, so the elapsed time is 0 and the guard
  // returned '' whether or not `sustained` was consulted. Deleting the sustained check left every
  // test green. The observation count is the intent; elapsed time is not a second opinion on it.
  if (!sustained) return '';
  const seconds = Math.round((now - unreachableSince) / 1000);
  if (seconds < 90) return ` for ${seconds}s`;
  const minutes = Math.round(seconds / 60);
  return minutes < 90 ? ` for ${minutes}m` : ` for ${Math.round(minutes / 60)}h`;
}

export function refreshChipState(
  settled,
  { previouslyFailed, names = REFRESH_SLICES, realtimeConnected = true, now = Date.now() } = {},
) {
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
      title: `Cannot reach the service${outageFor(now, before.has(names[AGENTS_SLICE]))}.`
        + (failed.length > 1 ? ` Not refreshed: ${failed.join(', ')}.` : ''),
      stale: [],
      failed,
    };
  }
  // Reachable again: the next outage is a NEW one and must not inherit this one's age.
  unreachableSince = null;

  if (!stale.length) {
    // REALTIME IS A SEPARATE QUESTION FROM FRESHNESS, and the chip answered only one of them.
    // `state.realtimeConnected` was written in four places by realtime-socket.mjs and read in
    // none: when the WebSocket dropped, the dashboard fell back to the 15s poll and this chip
    // went on saying `live` with the tooltip "All data refreshed" -- true of the poll, and read
    // by an operator as "updates are arriving as they happen". They were arriving up to a
    // refresh interval late, with nothing on screen to say so.
    //
    // Amber rather than green, because the view is behaving differently from how it looks; not
    // `reconnecting`, because the data IS current and the service IS reachable.
    if (realtimeConnected === false) {
      return {
        text: 'polling',
        className: 'status-chip warn',
        title: failed.length
          ? `Realtime updates are disconnected; refreshing on the poll. Retrying: ${failed.join(', ')}.`
          : 'Realtime updates are disconnected. The view refreshes on the poll instead.',
        stale: [],
        failed,
      };
    }
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
