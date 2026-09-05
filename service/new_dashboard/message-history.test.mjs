// Real tests for the on-demand history store.
//
// WHAT THEY PROVE, in the operator's terms: a conversation whose messages fall outside the fleet's
// newest-80 window can be scrolled back into view, no message is skipped at a page boundary, and the
// loop that fetches the pages always terminates.
//
// SEALED. `fetchPage` is injected, so nothing here touches the network or `state`; each test declares
// exactly what the server would have said.

import assert from "node:assert/strict";
import test from "node:test";

import { MessageHistory, anchoredScrollTop, mergeById, oldestTimestamp } from "./message-history.mjs";

const msg = (id, ts, extra = {}) => ({ id, timestamp: ts, from: "sc-manager", to: "dashboard", ...extra });

/** A server holding `total` messages, answering exactly as `/messages/recent` does. */
function server(total, limit = 10) {
  const all = Array.from({ length: total }, (_, i) => msg(`m${i}`, 1000 + (total - i))); // newest first
  const calls = [];
  return {
    all,
    calls,
    fetchPage: async (before) => {
      calls.push(before);
      const matching = all.filter((m) => before === null || m.timestamp <= before);
      return { messages: matching.slice(0, limit), truncated: matching.length > limit };
    },
  };
}

test("oldestTimestamp finds the bottom of the list, and says so when there is none", () => {
  assert.equal(oldestTimestamp([msg("a", 30), msg("b", 10), msg("c", 20)]), 10);
  assert.equal(oldestTimestamp([]), null);
  assert.equal(oldestTimestamp(null), null);
  // A row with no usable timestamp must not become the cursor -- NaN would be sent as `before`.
  assert.equal(oldestTimestamp([msg("a", 30), { id: "b" }, msg("c", "nonsense")]), 30);
});

test("mergeById keeps one copy of each message and lets the LIVE row win", () => {
  const live = [msg("a", 30, { read: true })];
  const history = [msg("a", 30, { read: false }), msg("b", 20)];
  const merged = mergeById(live, history);
  assert.deepEqual(merged.map((m) => m.id), ["a", "b"]);
  assert.equal(merged[0].read, true, "a stale history copy must not mark a read message unread again");
});

test("mergeById drops rows with no id rather than duplicating them", () => {
  // Two id-less rows are indistinguishable, so keeping them would grow the timeline on every page.
  assert.deepEqual(mergeById([{ timestamp: 1 }], [{ timestamp: 1 }]), []);
});

test("loadOlder fetches the page BELOW what is already held", async () => {
  const s = server(30);
  const history = new MessageHistory(s.fetchPage);
  const live = s.all.slice(0, 10); // what the poll already put on screen
  const added = await history.loadOlder(live);
  assert.equal(s.calls[0], oldestTimestamp(live), "it must page back from the oldest message on screen");
  assert.ok(added > 0);
  assert.ok(history.combined(live).length > live.length, "the conversation got deeper");
});

test("paging to the end reaches EVERY message, with no gap and no duplicate", async () => {
  // The operator's actual complaint, in miniature: 137 messages, a window that shows some of them.
  const s = server(137, 20);
  const history = new MessageHistory(s.fetchPage);
  const live = s.all.slice(0, 20);

  let guard = 0;
  while (!history.exhausted && guard < 100) { await history.loadOlder(live); guard += 1; }

  const seen = history.combined(live);
  assert.equal(seen.length, 137, "paging back did not reach the whole conversation");
  assert.equal(new Set(seen.map((m) => m.id)).size, 137, "a message was served twice");
  assert.ok(guard < 100, "the paging loop did not terminate on its own");
});

test("it stops asking once the server says the page was not truncated", async () => {
  const s = server(15, 20); // everything fits in one page
  const history = new MessageHistory(s.fetchPage);
  await history.loadOlder(s.all.slice(0, 5));
  assert.equal(history.exhausted, true);
  const callsBefore = s.calls.length;
  assert.equal(await history.loadOlder(s.all.slice(0, 5)), 0, "an exhausted store must not re-fetch");
  assert.equal(s.calls.length, callsBefore, "it asked the server again after reaching the end");
});

test("A PAGE OF PURE OVERLAP ENDS THE LOOP instead of repeating forever", async () => {
  // The hazard the inclusive cursor creates: a server that keeps returning rows already held, while
  // still reporting `truncated: true`, would be asked for the same page on every scroll. Without the
  // no-progress stop this test hangs rather than fails, which is why the loop below is bounded.
  const held = [msg("a", 100), msg("b", 100)];
  let calls = 0;
  const history = new MessageHistory(async () => {
    calls += 1;
    return { messages: held, truncated: true };
  });
  await history.loadOlder(held);
  assert.equal(history.exhausted, true, "no new messages arrived, so there is nothing further back");
  await history.loadOlder(held);
  assert.equal(calls, 1, "it asked again for a page it had already been given");
});

test("nothing on screen means nothing to page back from", async () => {
  // Paging from an empty timeline would re-fetch the newest page, which the poll already owns.
  let called = false;
  const history = new MessageHistory(async () => { called = true; return { messages: [], truncated: false }; });
  assert.equal(await history.loadOlder([]), 0);
  assert.equal(called, false);
  assert.equal(history.exhausted, false, "an empty screen is not an exhausted history");
});

test("a second scroll while the first is still in flight does not double-fetch", async () => {
  // EVERY call's resolver is kept, not just the latest. Keeping one variable meant a second fetch
  // overwrote the first one's resolver and nothing could ever settle it -- so removing the guard
  // under test made this file HANG and report "8 pass, 0 fail, 3 cancelled", which is not a red.
  // A guard whose absence cancels the run is a guard nobody has watched fail.
  const pending = [];
  const history = new MessageHistory(() => new Promise((r) => pending.push(r)));
  const live = [msg("a", 100)];

  const first = history.loadOlder(live);
  assert.equal(history.loading, true);
  const second = history.loadOlder(live);

  assert.equal(pending.length, 1, "a scroll during an in-flight page must not open a second request");
  pending.forEach((r) => r({ messages: [msg("b", 90)], truncated: true }));
  assert.equal(await second, 0, "the second call must not report progress it did not make");
  assert.equal(await first, 1);
});

test("reset forgets the pages so a reload starts from the live window", async () => {
  const s = server(30);
  const history = new MessageHistory(s.fetchPage);
  const live = s.all.slice(0, 10);
  await history.loadOlder(live);
  assert.ok(history.rows.length > 0);
  history.reset();
  assert.deepEqual(history.rows, []);
  assert.equal(history.exhausted, false);
  assert.deepEqual(history.combined(live).map((m) => m.id), live.map((m) => m.id));
});

test("a failed page leaves the store loadable rather than stuck", async () => {
  // `loading` is released in a finally, so one network error does not permanently disable scrollback.
  const history = new MessageHistory(async () => { throw new Error("offline"); });
  const live = [msg("a", 100)];
  await assert.rejects(() => history.loadOlder(live), /offline/);
  assert.equal(history.loading, false);
  assert.equal(history.exhausted, false);
});

test("anchoredScrollTop keeps the operator's place when history is prepended above them", () => {
  // 400px tall, scrolled to 100. A page of history adds 600px above; the same content must stay put,
  // which means scrollTop moves DOWN by exactly what was inserted.
  assert.equal(anchoredScrollTop(400, 100, 1000), 700);
  // Nothing was added: nothing moves.
  assert.equal(anchoredScrollTop(400, 100, 400), 100);
  // A timeline that SHRANK cannot scroll above the top.
  assert.equal(anchoredScrollTop(1000, 0, 200), 0);
  // Garbage in from a detached element must not produce NaN, which the DOM would take as 0 silently.
  assert.equal(anchoredScrollTop(undefined, 0, 500), 0);
});

test("a re-fetched history row takes the server's REFRESHED copy, not the one already held", async () => {
  // R9-M11. The cursor is inclusive, so every page boundary re-fetches a row the store already has.
  // Keeping the held copy discarded its own refresh: read state, edits and unsends on paged-in
  // messages stayed frozen until a full reload, and an unsent message kept rendering.
  const pages = [
    { messages: [msg("a", 300), msg("b", 200, { read: false, body: "before" })], truncated: true },
    { messages: [msg("b", 200, { read: true, body: "AFTER" }), msg("c", 100)], truncated: false },
  ];
  let n = 0;
  const history = new MessageHistory(async () => pages[n++]);
  const live = [msg("z", 400)];

  await history.loadOlder(live);
  await history.loadOlder(live);

  const b = history.combined(live).find((m) => m.id === "b");
  assert.equal(b.body, "AFTER", "the refreshed copy was dropped in favour of the stale one");
  assert.equal(b.read, true, "read state on a paged-in row stayed frozen");
});

test("...but the LIVE window still outranks history, which is the opposite rule", async () => {
  // The two directions are deliberate and easy to conflate. Inside the store the incoming page is
  // fresher; against the poll the live row is. Collapsing them either way loses one of the fixes.
  const history = new MessageHistory(async () => ({
    messages: [msg("a", 100, { read: false, body: "history copy" })],
    truncated: false,
  }));
  const live = [msg("a", 100, { read: true, body: "live copy" })];
  await history.loadOlder(live);

  const a = history.combined(live).find((m) => m.id === "a");
  assert.equal(a.body, "live copy", "a stale history copy overwrote the live row");
  assert.equal(a.read, true, "and would have marked a read message unread again");
});
