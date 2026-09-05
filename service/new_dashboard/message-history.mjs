// Older direct messages, fetched on demand when the operator scrolls back through a conversation.
//
// THE BUG THIS EXISTS FOR, operator-reported 2026-09-05: "I see only small part of messages in
// dashboard. it feels like upper messages get deleted... manager sent me lots of messages and I
// cannot see them." Nothing was deleted. The poll fetches ONE global window -- `/messages/recent`
// with `limit=80`, the newest 80 rows across the WHOLE FLEET -- into `state.messages`, and the DM
// view filters that window down to one peer. So a conversation's visible depth is not 80; it is
// however many of its messages happen to fall inside the fleet's newest 80. Measured that day: 137
// messages from `sc-manager`, 43 of them inside the window.
//
// KEPT SEPARATE FROM `state.messages` ON PURPOSE. The poll REPLACES that array every cycle, which is
// what makes deleted and edited messages disappear correctly. Merging history into it would either
// be wiped on the next tick or, if the poll were changed to merge, would resurrect every message the
// service had dropped. So the live window stays exactly as it was and history accumulates beside it;
// `combined()` is the only place the two meet.
//
// THE CURSOR IS INCLUSIVE, so a page can repeat rows already held -- see the endpoint's own comment
// for why an exclusive one would silently drop messages sharing a millisecond. Everything here
// de-duplicates by id, and `mergeById` is the single place that happens.

/**
 * How many messages one page of `/messages/recent` holds.
 *
 * ONE OWNER, because the poll and the pager must agree. The poll asked for `limit=80` as a literal
 * in its URL; a pager with its own literal would agree until somebody changed one of them, and the
 * symptom would be a scrollback that skips or repeats a block at every page boundary. The endpoint
 * caps it at 250.
 */
export const RECENT_PAGE_LIMIT = 80;

/** The oldest timestamp in a list, or null when there is nothing to page back from. */
export function oldestTimestamp(messages) {
  let oldest = null;
  for (const m of messages || []) {
    const ts = Number(m?.timestamp);
    if (!Number.isFinite(ts)) continue;
    if (oldest === null || ts < oldest) oldest = ts;
  }
  return oldest;
}

/**
 * Union of two message lists, keyed by id, with `existing` winning a collision.
 *
 * EXISTING WINS because the live poll's copy is the fresher one: it carries the current read state
 * and any edit, while a history page is a snapshot from whenever it was fetched. A history row
 * overwriting a live row would make a message the operator just read show as unread again.
 */
export function mergeById(existing, incoming) {
  const out = [];
  const seen = new Set();
  for (const m of existing || []) {
    const id = m?.id;
    if (id == null || seen.has(id)) continue;
    seen.add(id);
    out.push(m);
  }
  for (const m of incoming || []) {
    const id = m?.id;
    if (id == null || seen.has(id)) continue;
    seen.add(id);
    out.push(m);
  }
  return out;
}

/**
 * The pages of history loaded so far, and whether there are more to ask for.
 *
 * A class rather than loose functions because this is state with identity: a cursor that advances, an
 * in-flight guard, and an exhausted flag that must survive across renders. `fetchPage` is injected so
 * the whole thing runs in Node without a network.
 */
export class MessageHistory {
  #rows = [];
  #exhausted = false;
  #loading = false;
  #fetchPage;

  constructor(fetchPage) {
    this.#fetchPage = fetchPage;
  }

  get rows() { return this.#rows; }
  get loading() { return this.#loading; }

  /** True once paging has reached the beginning of history — the caller stops asking. */
  get exhausted() { return this.#exhausted; }

  /** Everything the timeline may show: the live window first, then what has been paged in. */
  combined(live) { return mergeById(live, this.#rows); }

  /**
   * Forget every page, so a reload starts from the live window again.
   *
   * NOT called on conversation switch. History is fetched globally, exactly as the live window is,
   * so pages loaded while reading one conversation are equally valid for the next -- dropping them
   * would re-fetch the same rows every time the operator changed chats.
   */
  reset() {
    this.#rows = [];
    this.#exhausted = false;
  }

  /**
   * Fetch the page older than everything currently held.
   *
   * Returns the number of NEW messages added, so a caller can tell "there was more" from "that was
   * all" without reading private state.
   */
  async loadOlder(live) {
    if (this.#loading || this.#exhausted) return 0;
    const cursor = oldestTimestamp(this.combined(live));
    // NO CURSOR MEANS NOTHING IS LOADED YET, and paging back from nowhere would fetch the newest
    // page a second time. The poll owns that page; this only ever asks for what is older.
    if (cursor === null) return 0;

    this.#loading = true;
    try {
      const page = await this.#fetchPage(cursor);
      const rows = page?.messages || [];
      // PROGRESS IS MEASURED AGAINST WHAT THE OPERATOR CAN ALREADY SEE, not against this store's own
      // rows. A page that merely repeats the live window is new to `#rows` and new to nothing else --
      // counting it as progress would let the no-advance guard below never fire, which is the loop it
      // exists to break.
      const before = this.combined(live).length;
      // THE INCOMING PAGE WINS HERE, and that is the opposite of `combined()` on purpose.
      //
      // `combined()` lets the LIVE window win because the poll's copy is the fresher one. Inside the
      // store the freshness runs the other way: `rows` is what the server just said, and `this.#rows`
      // is whatever it said last time. Keeping the old copy meant a re-fetched row -- and the cursor
      // is inclusive, so every page boundary re-fetches one -- silently discarded its own refresh,
      // freezing read state, edits and unsends on paged-in messages until a reload. R9-M11, external
      // review 2026-09-05; I had documented only the half of this rule I had thought about.
      this.#rows = mergeById(rows, this.#rows);
      const added = this.combined(live).length - before;

      // TWO WAYS TO BE DONE, and both are needed. `truncated: false` is the server saying this page
      // reached the end. `added === 0` covers the case the flag cannot: an inclusive cursor re-serving
      // rows already held would otherwise ask for the same page forever, so no progress means stop.
      // Stopping early is the safe failure here -- the operator sees a "load older" that does nothing
      // rather than a tab spinning on a loop it cannot leave.
      if (!page?.truncated || added === 0) this.#exhausted = true;
      return added;
    } finally {
      this.#loading = false;
    }
  }
}

/**
 * Where to leave the scrollbar after older messages are prepended.
 *
 * Prepending grows the timeline ABOVE the operator's reading position, so keeping `scrollTop` where
 * it was would teleport them backwards by exactly the height of the page just loaded -- which reads
 * as the view jumping every time more history arrives. What must stay constant is the distance from
 * the BOTTOM, so the message they were looking at stays under their eyes.
 *
 * Clamped at zero: a shrinking timeline (a poll that dropped rows mid-load) would otherwise compute
 * a negative offset, which the browser silently treats as 0 anyway -- said here so the intent is
 * readable rather than inherited from the DOM.
 */
export function anchoredScrollTop(previousHeight, previousTop, newHeight) {
  const distanceFromBottom = Number(previousHeight) - Number(previousTop);
  const next = Number(newHeight) - distanceFromBottom;
  return Number.isFinite(next) ? Math.max(0, next) : 0;
}

/**
 * The dashboard's history store, wired to the endpoint it pages.
 *
 * THE URL LIVES HERE, not in app.js. The orchestrator's job is to say WHICH store the chat gets, not
 * to know how a page of history is addressed — and app.js was 13 lines from the 1000-line gate, so a
 * six-line fetch expression in it was borrowed space either way. `api` is injected rather than
 * imported so this module still loads and tests in Node with no network.
 */
export function createMessageHistory(api) {
  return new MessageHistory((before) => api(
    `/messages/recent?limit=${RECENT_PAGE_LIMIT}&before=${encodeURIComponent(before)}`,
  ));
}
