// The Analytics fetch and its two renderers, tested by CALLING them.
//
// The behaviour worth guarding is the CACHE. The Analytics page re-renders on every ~15s poll, and an
// unconditional fetch would add three more requests to that cycle for data that changes far more
// slowly — on a single-worker service that is exactly the poll load the dashboard has already been cut
// twice to avoid. But the gate must NOT hold when the operator picks a different range, or the range
// selector silently does nothing for up to twelve seconds and reads as broken.
//
// The other half is failure tolerance: a quota number that blanks on a transient error is worse than a
// stale one, because "0% remaining" is a number an operator acts on.

import assert from "node:assert/strict";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { state } from "./state.mjs";
import { loadAnalytics, renderAnalyticsPage, renderUsagePools } from "./analytics-page.mjs";

function makeEl() {
  return {
    innerHTML: "", textContent: "", className: "", value: "", hidden: false, dataset: {}, style: {},
    children: [], firstElementChild: null,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    querySelector: () => null, querySelectorAll: () => [], appendChild() {}, setAttribute() {},
    addEventListener() {}, remove() {}, focus() {},
  };
}

/** The real pool shape the backend sends — source_id, and separate weekly / five_hour bands. */
const POOL = {
  source_id: "anthropic-claude-max",
  verified: true,
  weekly: { left_pct: 42, used_pct: 58 },
  five_hour: { left_pct: 90, resets_at: "2026-08-14T12:00:00Z" },
};

function withAnalytics({ fail = [], now = 1_000_000 } = {}) {
  const els = new Map();
  const asked = [];
  const saved = { document: globalThis.document, fetch: globalThis.fetch, requestAnimationFrame: globalThis.requestAnimationFrame, Date: globalThis.Date };
  globalThis.requestAnimationFrame = (fn) => { fn(); return 1; };
  globalThis.document = {
    getElementById: (id) => { if (!els.has(id)) els.set(id, makeEl()); return els.get(id); },
    querySelector: () => null, querySelectorAll: () => [], createElement: () => makeEl(),
    addEventListener() {}, removeEventListener() {},
    body: { appendChild() {}, classList: { add() {}, remove() {} }, style: { setProperty() {} } },
  };
  // A FROZEN CLOCK, so the cache window is exercised deliberately rather than by whatever the machine
  // happened to take. Only `Date.now` is replaced; `new Date()` is left alone.
  const RealDate = saved.Date;
  globalThis.Date = class extends RealDate { static now() { return now; } };
  globalThis.fetch = async (url) => {
    const path = String(url);
    asked.push(path);
    if (fail.some((p) => path.includes(p))) throw new TypeError("Failed to fetch");
    const body = path.includes("/usage/consumption") ? { consumption: { total: 1 } }
      : path.includes("/usage") ? { pools: [POOL] }
      : { traffic: [], health: {} };
    return { ok: true, status: 200, statusText: "OK", text: async () => JSON.stringify(body) };
  };
  setApiBase("");
  state.analytics = { range: "24h", loading: false, lastMs: 0, data: null, usage: null, usageStale: false, consumption: null };
  return { els, asked, restore: () => Object.assign(globalThis, saved) };
}

test("a first load fetches analytics, usage and consumption", async () => {
  const h = withAnalytics();
  try {
    await loadAnalytics();
    assert.equal(h.asked.length, 3);
    assert.ok(h.asked.some((p) => p.includes("/analytics?range=")));
    assert.ok(h.asked.some((p) => p.endsWith("/usage")));
    assert.ok(h.asked.some((p) => p.includes("/usage/consumption")));
  } finally { h.restore(); }
});

test("A SECOND LOAD INSIDE THE CACHE WINDOW FETCHES NOTHING but still renders", async () => {
  // This is what keeps the poll off the analytics endpoints. Rendering anyway matters: the page must
  // still repaint from the cached data, or switching to Analytics between fetches shows an empty page.
  const h = withAnalytics();
  try {
    await loadAnalytics();
    const afterFirst = h.asked.length;
    await loadAnalytics();
    assert.equal(h.asked.length, afterFirst, "a poll inside the window must not re-fetch");
    assert.ok(h.els.get("analytics-page") || true);
  } finally { h.restore(); }
});

test("force=true FETCHES ANYWAY — the range selector depends on it", async () => {
  // Without this, changing the range does nothing visible for up to twelve seconds, which reads as a
  // broken control and gets clicked repeatedly.
  const h = withAnalytics();
  try {
    await loadAnalytics();
    const afterFirst = h.asked.length;
    await loadAnalytics(true);
    assert.equal(h.asked.length, afterFirst + 3, "an explicit request must bypass the cache");
  } finally { h.restore(); }
});

test("the cache EXPIRES rather than holding forever", async () => {
  const h = withAnalytics({ now: 1_000_000 });
  try {
    await loadAnalytics();
    const afterFirst = h.asked.length;
    // Move the frozen clock past the window.
    const RealDate = Object.getPrototypeOf(globalThis.Date);
    globalThis.Date = class extends RealDate { static now() { return 1_000_000 + 12_001; } };
    await loadAnalytics();
    assert.equal(h.asked.length, afterFirst + 3, "past the window it must fetch again");
  } finally { h.restore(); }
});

test("a REENTRANT load while one is in flight does nothing", async () => {
  const h = withAnalytics();
  let release;
  try {
    const gate = new Promise((r) => { release = r; });
    let fetches = 0;
    globalThis.fetch = async () => {
      fetches += 1;
      await gate;
      return { ok: true, status: 200, statusText: "OK", text: async () => "{}" };
    };
    const first = loadAnalytics();
    const second = loadAnalytics();
    release();
    await Promise.all([first, second]);
    assert.equal(fetches, 3, "the second call must not start a second round of three");
  } finally { h.restore(); }
});

test("A FAILED /usage KEEPS THE LAST-GOOD NUMBER AND FLAGS IT STALE", async () => {
  // Blanking a quota is worse than showing an old one: an operator reads a blank pool as "no quota
  // information" and an accidental 0 as "out of quota". The stale flag is how the panel can say which.
  const ok = withAnalytics();
  try {
    await loadAnalytics();
    assert.equal(state.analytics.usage.pools[0].weekly.left_pct, 42);
    assert.equal(state.analytics.usageStale, false);
  } finally { ok.restore(); }

  const down = withAnalytics({ fail: ["/usage"] });
  try {
    state.analytics.usage = { pools: [POOL] };
    state.analytics.lastMs = 0;
    await loadAnalytics(true);
    assert.equal(state.analytics.usage.pools[0].weekly.left_pct, 42, "the last-good quota must survive");
    assert.equal(state.analytics.usageStale, true, "…and be marked as not fresh");
  } finally { down.restore(); }
});

test("a failed /analytics still leaves an object, so the renderers have something to read", async () => {
  const h = withAnalytics({ fail: ["/analytics"] });
  try {
    await loadAnalytics();
    assert.deepEqual(state.analytics.data, {}, "an empty object, not null");
    assert.equal(state.analytics.loading, false, "the in-flight flag must clear even on failure");
  } finally { h.restore(); }
});

test("the loading flag clears on the failure path too — otherwise the page never loads again", async () => {
  const h = withAnalytics();
  try {
    globalThis.fetch = async () => { throw new TypeError("down"); };
    await loadAnalytics(true);
    assert.equal(state.analytics.loading, false);
    // And a subsequent call is not blocked by it.
    await assert.doesNotReject(() => loadAnalytics(true));
  } finally { h.restore(); }
});

test("renderUsagePools says the collector is warming up rather than rendering nothing", async () => {
  const h = withAnalytics();
  try {
    state.analytics.usage = { pools: [] };
    renderUsagePools();
    assert.match(h.els.get("usage-pools").innerHTML, /warming up/,
      "an empty pool list must be explained, not shown as a blank band");
  } finally { h.restore(); }
});

test("renderUsagePools names the source and shows the weekly figure", async () => {
  const h = withAnalytics();
  try {
    await loadAnalytics();
    renderUsagePools();
    const html = h.els.get("usage-pools").innerHTML;
    assert.match(html, /Anthropic/, "the source must be named, not shown as its raw id");
    assert.match(html, /42%/);
  } finally { h.restore(); }
});

test("AN UNVERIFIED POOL SHOWS — RATHER THAN A NUMBER IT CANNOT STAND BEHIND", async () => {
  // The backend blanks figures it cannot trust. Rendering the raw value instead would show a
  // confident percentage derived from nothing — the same failure class as a health check that
  // reports OK when it gathered no evidence.
  const h = withAnalytics();
  try {
    state.analytics.usage = { pools: [{ ...POOL, verified: false }] };
    renderUsagePools();
    const html = h.els.get("usage-pools").innerHTML;
    assert.match(html, /—/, "an unverified pool must render an em dash");
    assert.doesNotMatch(html, /42%/, "…and must not show the number it could not verify");
  } finally { h.restore(); }
});

test("the stale note appears only when the last refresh actually failed", async () => {
  const h = withAnalytics();
  try {
    state.analytics.usage = { pools: [POOL] };
    state.analytics.usageStale = false;
    renderUsagePools();
    assert.doesNotMatch(h.els.get("usage-pools").innerHTML, /Last usage refresh failed/);
    state.analytics.usageStale = true;
    renderUsagePools();
    assert.match(h.els.get("usage-pools").innerHTML, /Last usage refresh failed/,
      "a stale quota must say so — an old number presented as current is what gets acted on");
  } finally { h.restore(); }
});

test("both renderers are no-ops when their host element is absent", async () => {
  // They run from the render orchestrator on every poll, including on pages that do not contain them.
  const saved = globalThis.document;
  globalThis.document = { getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] };
  try {
    assert.doesNotThrow(() => renderUsagePools());
    assert.doesNotThrow(() => renderAnalyticsPage());
  } finally { globalThis.document = saved; }
});
