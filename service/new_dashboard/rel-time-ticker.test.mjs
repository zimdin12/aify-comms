// The relative-time ticker, and the pure refresh it drives.
//
// WHY THIS EXISTS. `render-memo.mjs` says it in its own words: "RELATIVE TIMES ARE DELIBERATELY NOT
// SOLVED HERE ... No signature can express that ... Left as v0.6.1 work rather than half-done here."
// A memoised section repaints when a FIELD moves, and "4m ago" must move when no field does. Adding
// `lastSeen` to the signature repaints only when the heartbeat lands; adding a clock tick repaints
// every section on a timer and destroys selection and focus while doing it.
//
// So the text is updated WITHOUT a render: each rendered time carries its own absolute timestamp in
// `data-rel-ts`, and one ticker rewrites `textContent` in place. No innerHTML, no rebuilt DOM, no
// lost selection.

import assert from "node:assert/strict";
import test from "node:test";

import { relTimeAt, relTimeHtml } from "./util.js";
import {
  refreshRelTimes, startRelTimeTicker, REL_TIME_SELECTOR, REL_TIME_INTERVAL_MS,
} from "./rel-time-ticker.mjs";

// A stand-in for the one property the refresh touches. Deliberately NOT a full DOM: the refresh is
// written against `dataset` + `textContent` precisely so it can be driven without a browser.
const el = (relTs, text = "") => ({ dataset: relTs === null ? {} : { relTs: String(relTs) }, textContent: text });

const T0 = Date.UTC(2026, 7, 31, 12, 0, 0);

// ------------------------------------------------------------------ relTimeAt: the clock is an input

test("relTimeAt takes the clock as an argument, so one moment gives one answer", () => {
  assert.equal(relTimeAt(T0 - 5 * 60000, T0), "5m");
  assert.equal(relTimeAt(T0 - 5 * 3600000, T0), "5h");
  assert.equal(relTimeAt(T0 - 5 * 86400000, T0), "5d");
});

test("relTimeAt clamps a FUTURE timestamp to 0m rather than going negative", () => {
  assert.equal(relTimeAt(T0 + 600000, T0), "0m");
});

test("relTimeAt returns empty for missing or unparseable input", () => {
  for (const value of [null, undefined, "", "nope"]) assert.equal(relTimeAt(value, T0), "");
});

// ------------------------------------------------------------------ relTimeHtml: the timestamp travels

test("relTimeHtml carries the PARSED epoch-ms, so the ticker never re-parses a mixed shape", () => {
  // The API returns epoch-seconds, epoch-ms and ISO text. Parsing once at render and shipping the
  // resolved number is what keeps the ticker a pure arithmetic step.
  const html = relTimeHtml("2026-08-31T11:55:00Z", T0);
  assert.match(html, /data-rel-ts="1788177300000"/);
  assert.match(html, />5m</);
});

test("relTimeHtml returns EMPTY for unusable input, so `x ? ... : ''` call sites keep working", () => {
  // Several call sites read `relTime(x) ? ... : ''`. Emitting an empty span here would make every one
  // of those conditionals true and print a stray separator.
  for (const value of [null, undefined, "", "nope"]) assert.equal(relTimeHtml(value, T0), "");
});

test("relTimeHtml marks the span with the class the ticker selects on", () => {
  assert.ok(relTimeHtml(T0 - 60000, T0).includes(REL_TIME_SELECTOR.replace(".", "")));
});

// ------------------------------------------------------------------ refreshRelTimes

test("refreshRelTimes rewrites the text from the stored timestamp", () => {
  const node = el(T0 - 3 * 60000, "0m");
  refreshRelTimes([node], T0);
  assert.equal(node.textContent, "3m");
});

test("an element whose text is ALREADY correct is not written to", () => {
  // Writing textContent unnecessarily is not free: it is a DOM mutation under an operator who may be
  // mid-selection, which is the whole reason the memo exists. Only changed nodes are touched.
  let writes = 0;
  const node = {
    dataset: { relTs: String(T0 - 3 * 60000) },
    get textContent() { return "3m"; },
    set textContent(v) { writes += 1; },
  };
  refreshRelTimes([node], T0);
  assert.equal(writes, 0, "an unchanged time was written anyway");
});

test("refreshRelTimes reports how many it changed", () => {
  const changed = el(T0 - 3 * 60000, "0m");
  const same = el(T0 - 60000, "1m");
  assert.equal(refreshRelTimes([changed, same], T0), 1);
});

test("an element with a MISSING timestamp is left alone, never blanked", () => {
  // Fail closed. A guard that overwrites when its input is absent turns one missing attribute into a
  // wiped label, which reads as "no data" rather than as a bug.
  const node = el(null, "whatever");
  refreshRelTimes([node], T0);
  assert.equal(node.textContent, "whatever");
});

test("an element with an UNPARSEABLE timestamp is left alone too", () => {
  const node = el("not-a-number", "whatever");
  refreshRelTimes([node], T0);
  assert.equal(node.textContent, "whatever");
});

test("refreshRelTimes survives a nodeless argument rather than throwing into the poll loop", () => {
  assert.equal(refreshRelTimes(null, T0), 0);
  assert.equal(refreshRelTimes(undefined, T0), 0);
});

// ------------------------------------------------------------------ the ticker drives the refresh

test("the ticker asks for the nodes and refreshes them on every tick", () => {
  // This is the CALL SITE, not the helper: a green refresh proves nothing if the ticker never calls it.
  const node = el(T0 - 60000, "");
  let queried = 0;
  const timers = [];
  const stop = startRelTimeTicker({
    queryAll: () => { queried += 1; return [node]; },
    nowMs: () => T0,
    intervalMs: 1000,
    setIntervalImpl: (fn) => { timers.push(fn); return 1; },
    clearIntervalImpl: () => { timers.length = 0; },
  });
  assert.equal(queried, 1, "the ticker did not paint once on start");
  assert.equal(node.textContent, "1m");

  node.dataset.relTs = String(T0 - 7 * 60000);
  timers[0]();
  assert.equal(queried, 2, "the interval did not re-query");
  assert.equal(node.textContent, "7m");
  stop();
});

test("the ticker re-queries every tick, so times rendered AFTER it started are picked up", () => {
  // The node list is not captured once. A section that repaints replaces its elements, and a ticker
  // holding the old ones would silently stop updating exactly the sections that are most active.
  const first = el(T0 - 60000, "");
  const second = el(T0 - 2 * 60000, "");
  let batch = [first];
  const timers = [];
  const stop = startRelTimeTicker({
    queryAll: () => batch,
    nowMs: () => T0,
    intervalMs: 1000,
    setIntervalImpl: (fn) => { timers.push(fn); return 1; },
    clearIntervalImpl: () => {},
  });
  batch = [first, second];
  timers[0]();
  assert.equal(second.textContent, "2m", "a newly rendered time was never updated");
  stop();
});

test("a throwing queryAll does not kill the ticker", () => {
  // It shares a page with the poll loop. One bad frame must not stop every relative time on the
  // dashboard for the rest of the session -- the latching failure render-memo.mjs was fixed for.
  const timers = [];
  let calls = 0;
  const stop = startRelTimeTicker({
    queryAll: () => { calls += 1; if (calls === 1) throw new Error("boom"); return []; },
    nowMs: () => T0,
    intervalMs: 1000,
    setIntervalImpl: (fn) => { timers.push(fn); return 1; },
    clearIntervalImpl: () => {},
  });
  assert.equal(timers.length, 1, "the ticker never armed after a failed first paint");
  timers[0]();
  assert.equal(calls, 2);
  stop();
});

test("the timer is unref'd when the platform offers it, so it never holds a process open", () => {
  // Regression: wiring this into `boot-wiring.mjs` with a live `setInterval` hung `node --test`
  // outright. A hung runner prints no result at all, which is worse than a red one.
  let unrefs = 0;
  const stop = startRelTimeTicker({
    queryAll: () => [],
    nowMs: () => T0,
    intervalMs: 1000,
    setIntervalImpl: () => ({ unref: () => { unrefs += 1; } }),
    clearIntervalImpl: () => {},
  });
  assert.equal(unrefs, 1, "the ticker never unref'd its timer");
  stop();
});

test("a platform whose timer has no unref (a browser number) is handled, not crashed on", () => {
  const stop = startRelTimeTicker({
    queryAll: () => [],
    nowMs: () => T0,
    intervalMs: 1000,
    setIntervalImpl: () => 42,
    clearIntervalImpl: () => {},
  });
  stop();
});

test("a caller that names no interval gets the declared default, which is what boot actually uses", () => {
  // `boot-wiring.mjs` starts this with `queryAll` alone, so the DEFAULT is the value that runs on the
  // operator's screen. An untested default is the one nobody notices drifting.
  let armedWith = null;
  const stop = startRelTimeTicker({
    queryAll: () => [],
    nowMs: () => T0,
    setIntervalImpl: (_fn, ms) => { armedWith = ms; return 1; },
    clearIntervalImpl: () => {},
  });
  assert.equal(armedWith, REL_TIME_INTERVAL_MS);
  // A minute-resolution label needs no faster tick, and a faster one is pure wakeups.
  assert.ok(REL_TIME_INTERVAL_MS >= 5000 && REL_TIME_INTERVAL_MS <= 60000,
            `the default tick is ${REL_TIME_INTERVAL_MS}ms, outside anything a "Nm ago" label needs`);
  stop();
});

test("stop() clears the interval it armed", () => {
  let cleared = null;
  const stop = startRelTimeTicker({
    queryAll: () => [],
    nowMs: () => T0,
    intervalMs: 1000,
    setIntervalImpl: () => 77,
    clearIntervalImpl: (handle) => { cleared = handle; },
  });
  stop();
  assert.equal(cleared, 77);
});
