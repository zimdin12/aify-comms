// Refreshing what changed, when it changed, instead of everything on a timer.
//
// MEASURED BEFORE, 2026-09-16: an open dashboard refetched ten endpoints every 15 seconds, and every
// pushed event except three refetched all ten again. The pushes were already sent on change; the
// timer and the whole-bundle refetch were the cost.
//
// WHAT DRIVES A REFRESH NOW:
//   * `data_changed` from the service (service/change_feed.py) names the tables a commit touched.
//     Only the slices that read them are fetched (slice-loaders.mjs), after a short debounce so a
//     burst of commits is one fetch.
//   * Liveness -- heartbeats, turn markers -- arrives in its own field and refreshes only the slices
//     that show an age, at most once a LIVENESS interval. `stats` is the one expensive endpoint and
//     is held to its own interval.
//   * A FULL refresh, once, whenever this client may have missed something: the socket reconnected,
//     a `seq` was skipped, or the service restarted (a new `instance`). The caller's full refresh
//     also runs when a hidden tab is shown again (refresh-visibility.mjs).
//   * The timed poll runs ONLY while the socket is down. It was the correctness guarantee; the gap
//     and instance checks are now, and a disconnected socket has neither.
//
// A class because this is a thing with state that changes -- a baseline, pending slices, timers --
// and every input arrives from a different place: the socket, the settings, the clock.

import { LIVENESS_SLICES, SLICE_TABLES, slicesReading } from './slice-tables.mjs';

export const CHANGE_DEBOUNCE_MS = 250;
/** How often a slice that only shows an age is refetched for liveness alone. */
export const LIVENESS_INTERVAL_MS = 60_000;
/** A slice's own floor, for the ones that cost more than the rest. `/stats` aggregates every message. */
export const SLICE_MIN_INTERVAL_MS = Object.freeze({ stats: 30_000 });
/** A slice whose fetch failed is tried again after this long, while the socket stays up. */
export const RETRY_AFTER_MS = 5_000;

export class ChangeDrivenRefresh {
  /**
   * @param {object} deps
   * @param {() => void} deps.fullRefresh           the whole bundle (app.js `refresh`)
   * @param {(slices: string[]) => Promise<string[]>} deps.refreshSlices  loads them; resolves the ones that FAILED
   * @param {() => number} deps.pollSeconds         the operator's refresh interval, read at every arm
   */
  constructor({ fullRefresh, refreshSlices, pollSeconds, now = () => Date.now(), timers = globalThis }) {
    this.fullRefresh = fullRefresh;
    this.refreshSlices = refreshSlices;
    this.pollSeconds = pollSeconds;
    this.now = now;
    this.timers = timers;
    this.connected = false;
    this.instance = null;
    this.seq = null;
    /** slice -> the earliest time it may be fetched. */
    this.pending = new Map();
    /** slice -> when the change that made it pending arrived. */
    this.wantedAt = new Map();
    /** slice -> when it was last fetched, by either path. */
    this.loadedAt = new Map();
    this.flushTimer = null;
    this.flushAt = 0;
    this.inFlight = false;
    this.pollTimer = null;
    /** Counts fetch starts, full and partial alike, so two of them are ordered even within one ms. */
    this.generation = 0;
    /** slice -> the generation of the fetch whose data it is showing. */
    this.shownGeneration = new Map();
  }

  /** Has this connection delivered a change yet? Until it has, a named event still refetches. */
  get covering() {
    return this.connected && this.instance !== null;
  }

  // ── the socket ─────────────────────────────────────────────────────────────────────────────────
  opened({ reconnected }) {
    this.connected = true;
    this.instance = null;
    this.seq = null;
    this.stopPolling();
    // A reconnect may have missed any number of changes. A first open follows the boot refresh.
    if (reconnected) this.recover();
  }

  closed() {
    this.connected = false;
    this.instance = null;
    this.seq = null;
    this.pending.clear();
    this.wantedAt.clear();
    this.cancelFlush();
    this.armPoll();
  }

  /** One `data_changed` event. */
  changed({ seq, instance, tables = [], liveness = [] } = {}) {
    if (!this.connected) return;
    const number = Number(seq);
    if (this.instance !== null && (instance !== this.instance || number !== this.seq + 1)) {
      // A skipped event, or a service that restarted and began counting again: what was missed is
      // unknown, so everything is fetched once and the count starts over from here.
      this.instance = instance;
      this.seq = number;
      this.recover();
      return;
    }
    this.instance = instance;
    this.seq = number;
    const now = this.now();
    for (const slice of slicesReading(tables)) this.want(slice, now + CHANGE_DEBOUNCE_MS);
    for (const slice of slicesReading(liveness, LIVENESS_SLICES)) {
      this.want(slice, Math.max(now + CHANGE_DEBOUNCE_MS, (this.loadedAt.get(slice) ?? 0) + LIVENESS_INTERVAL_MS));
    }
    this.scheduleFlush();
  }

  // ── what the caller reports ────────────────────────────────────────────────────────────────────
  /** A full bundle is starting now, from here or anywhere else. Hand what this returns to `fullyRefreshed`. */
  fullRefreshStarting() {
    return { startedAt: this.now(), generation: ++this.generation };
  }

  /**
   * The full bundle `started` (from `fullRefreshStarting`) finished. Every slice is current as of its
   * start -- not its end: a change that arrived while it was fetching may postdate the response that
   * slice got, so it stays pending.
   *
   * `failed` names the slices whose fetch failed in that bundle. They are NOT current, and while the
   * socket is up nothing else would fetch them until their tables next change -- a failed
   * `/settings/schema` at boot left Settings on "Loading settings..." indefinitely. They are retried
   * the way a failed partial refresh is.
   *
   * A slice a partial refresh STARTED LATER had already painted is now showing this bundle's older
   * data, and the change that partial fetched is no longer pending anywhere. It is fetched again.
   */
  fullyRefreshed({ startedAt, generation }, failed = []) {
    const overwritten = [];
    for (const slice of Object.keys(SLICE_TABLES)) {
      if (failed.includes(slice)) continue;
      if (this.shown(slice, generation)) this.loadedAt.set(slice, startedAt);
      else overwritten.push(slice);
    }
    for (const [slice, at] of [...this.wantedAt]) {
      if (at < startedAt) this.forget(slice);
    }
    if (this.connected) {
      for (const slice of failed) this.want(slice, this.now() + RETRY_AFTER_MS);
      for (const slice of overwritten) this.refetch(slice);
    }
    this.cancelFlush();
    this.scheduleFlush();
  }

  // ── the poll, only while disconnected ──────────────────────────────────────────────────────────
  armPoll() {
    if (this.connected) return;
    this.stopPolling();
    const seconds = Math.max(5, Number(this.pollSeconds()) || 15);
    this.pollTimer = this.timers.setTimeout(() => {
      this.pollTimer = null;
      this.fullRefresh();
      this.armPoll();
    }, seconds * 1000);
  }

  stopPolling() {
    if (this.pollTimer !== null) this.timers.clearTimeout(this.pollTimer);
    this.pollTimer = null;
  }

  // ── internals ──────────────────────────────────────────────────────────────────────────────────
  recover() {
    this.pending.clear();
    this.wantedAt.clear();
    this.cancelFlush();
    this.fullRefresh();
  }

  forget(slice) {
    this.pending.delete(slice);
    this.wantedAt.delete(slice);
  }

  /**
   * Record that `slice` now shows the data of the fetch numbered `generation`. False when a fetch
   * that started later had already painted it: this apply has just put older data over newer.
   */
  shown(slice, generation) {
    const overwroteNewer = (this.shownGeneration.get(slice) ?? 0) > generation;
    this.shownGeneration.set(slice, generation);
    return !overwroteNewer;
  }

  /** Fetch `slice` again soon, past its floor: what it shows is older than what was already fetched. */
  refetch(slice) {
    const due = this.now() + CHANGE_DEBOUNCE_MS;
    this.pending.set(slice, Math.min(this.pending.get(slice) ?? due, due));
    if (!this.wantedAt.has(slice)) this.wantedAt.set(slice, this.now());
  }

  want(slice, earliest) {
    const floor = (this.loadedAt.get(slice) ?? 0) + (SLICE_MIN_INTERVAL_MS[slice] ?? 0);
    const due = Math.max(earliest, floor);
    const already = this.pending.get(slice);
    // The sooner of two reasons wins: a real change never waits behind a liveness window.
    this.pending.set(slice, already === undefined ? due : Math.min(already, due));
    if (!this.wantedAt.has(slice)) this.wantedAt.set(slice, this.now());
  }

  scheduleFlush() {
    if (!this.pending.size || this.inFlight) return;
    const due = Math.min(...this.pending.values());
    if (this.flushTimer !== null && this.flushAt <= due) return;
    this.cancelFlush();
    this.flushAt = due;
    this.flushTimer = this.timers.setTimeout(() => {
      this.flushTimer = null;
      this.flush();
    }, Math.max(0, due - this.now()));
  }

  cancelFlush() {
    if (this.flushTimer !== null) this.timers.clearTimeout(this.flushTimer);
    this.flushTimer = null;
  }

  async flush() {
    if (!this.connected || this.inFlight) return;
    const now = this.now();
    const due = [...this.pending].filter(([, at]) => at <= now).map(([slice]) => slice);
    if (!due.length) { this.scheduleFlush(); return; }
    for (const slice of due) this.forget(slice);
    this.inFlight = true;
    const generation = ++this.generation;
    let failed = [];
    try {
      failed = (await this.refreshSlices(due)) || [];
    } catch (_) {
      failed = due;
    } finally {
      this.inFlight = false;
    }
    const finished = this.now();
    // A full bundle that started after this fetch and finished first has been painted over with this
    // fetch's older data, and it already cleared what it covered: fetched again, as in `fullyRefreshed`.
    const overwritten = [];
    for (const slice of due) {
      if (failed.includes(slice)) continue;
      if (this.shown(slice, generation)) this.loadedAt.set(slice, finished);
      else overwritten.push(slice);
    }
    // Only while connected: a socket that went away hands recovery to the poll and the reconnect.
    if (this.connected) {
      for (const slice of failed) this.want(slice, finished + RETRY_AFTER_MS);
      for (const slice of overwritten) this.refetch(slice);
    }
    this.scheduleFlush();
  }
}
