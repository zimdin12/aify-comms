// Navigation and analytics-range clicks, tested by CALLING them.
//
// All three were branch bodies inside app.js's delegated click handler, so nothing could reach them.
// Every one pairs a navigation with a side effect that must happen in the SAME click — and half of any
// of these looks exactly like a working button: the page changes, and the thing the page is for never
// loads.

import assert from "node:assert/strict";
import test from "node:test";

import { rangeDef } from "./analytics.js";
import { state } from "./state.mjs";
import {
  navigateToPage,
  openEnvironmentSpawn,
  selectAnalyticsRange,
} from "./nav-click-handlers.mjs";

/** Seal `state.analytics`, which is a shared singleton across the suite. */
function withAnalytics(run) {
  const saved = state.analytics;
  state.analytics = { ...(state.analytics ?? {}), range: "24h" };
  try {
    return run();
  } finally {
    state.analytics = saved;
  }
}

/** Install a DOM stub for the handlers that focus a field. */
function withDom(run) {
  const had = "document" in globalThis;
  const prev = globalThis.document;
  let focused = 0;
  globalThis.document = {
    getElementById: (id) => (id === "env-spawn-agent-id" ? { focus: () => { focused += 1; } } : null),
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  try {
    return run({ focused: () => focused });
  } finally {
    if (had) globalThis.document = prev; else delete globalThis.document;
  }
}

// --- selectAnalyticsRange ----------------------------------------------------------------------

test("selectAnalyticsRange stores the NORMALISED key, not the raw attribute", () => {
  // `rangeDef(raw).key`. Storing the attribute verbatim would let a typo'd or aliased value into
  // `state.analytics.range`, which is sent to the API as a window — and the chart would come back empty
  // rather than erroring.
  withAnalytics(() => {
    let loads = 0;
    const raw = "24h";
    selectAnalyticsRange({ dataset: { analyticsRange: raw } }, () => { loads += 1; });
    assert.equal(state.analytics.range, rangeDef(raw).key);
    assert.equal(loads, 1, "changing the range must refetch, or the chart keeps the old window");
  });
});

test("an UNKNOWN range still resolves to a valid key rather than storing junk", () => {
  // `rangeDef` owns the fallback. This asserts the handler defers to it instead of guarding itself,
  // which is what keeps one definition of the valid windows.
  withAnalytics(() => {
    selectAnalyticsRange({ dataset: { analyticsRange: "no-such-range" } }, () => {});
    assert.equal(state.analytics.range, rangeDef("no-such-range").key);
    assert.ok(state.analytics.range, "never empty");
  });
});

test("selectAnalyticsRange forces the reload rather than letting a cache answer", () => {
  // `loadAnalytics(true)`. The range just changed, so a cached response is by definition the wrong
  // window — this is the argument that makes the click do anything at all.
  withAnalytics(() => {
    const args = [];
    selectAnalyticsRange({ dataset: { analyticsRange: "24h" } }, (...a) => args.push(a));
    assert.deepEqual(args, [[true]]);
  });
});

// --- navigateToPage ----------------------------------------------------------------------------

test("navigateToPage switches the page", () => {
  const pages = [];
  navigateToPage("sessions", (p) => pages.push(p), () => {});
  assert.deepEqual(pages, ["sessions"]);
});

test("OPENING ANALYTICS LAZY-LOADS IT; every other page does not", () => {
  // The analytics page is the only one that fetches on open, and it refetches on RE-open. Without the
  // branch the page renders whatever was last loaded — most visibly, nothing at all on a fresh session.
  // Loading it for every page would put an analytics fetch behind every nav click instead.
  const loads = [];
  navigateToPage("analytics", () => {}, (...a) => loads.push(a));
  assert.deepEqual(loads, [[true]], "forced, so re-opening refreshes rather than reusing");

  loads.length = 0;
  for (const page of ["sessions", "work", "chat", "environments", "settings"]) {
    navigateToPage(page, () => {}, (...a) => loads.push(a));
  }
  assert.deepEqual(loads, [], "no other page triggers a load");
});

test("the analytics check is exact — a page merely CONTAINING the word does not load", () => {
  // `page === 'analytics'`. A prefix or substring test would fire on a future "analytics-detail" page
  // and double-fetch on every visit to it.
  const loads = [];
  for (const page of ["analytics-detail", "Analytics", "chat-analytics"]) {
    navigateToPage(page, () => {}, () => loads.push(page));
  }
  assert.deepEqual(loads, []);
});

// --- openEnvironmentSpawn ----------------------------------------------------------------------

test("openEnvironmentSpawn navigates, seeds the form, and FOCUSES the id field", () => {
  // Three effects in one click. The focus is the one that would be dropped without anyone noticing in
  // review, and it is the whole point of the shortcut: the operator lands ready to type an agent id.
  withDom((dom) => {
    const pages = [];
    const seeded = [];
    openEnvironmentSpawn(
      { dataset: { envSpawn: "env-1" } },
      (p) => pages.push(p),
      (e) => seeded.push(e),
    );
    assert.deepEqual(pages, ["environments"]);
    assert.deepEqual(seeded, ["env-1"], "the spawn form is pointed at the environment clicked");
    assert.equal(dom.focused(), 1);
  });
});

test("openEnvironmentSpawn survives the form field being absent", () => {
  // `byId(...)?.focus()`. The page has only just been switched to, so the field may not be in the DOM
  // yet — and throwing here would abandon the rest of the delegated handler.
  const had = "document" in globalThis;
  const prev = globalThis.document;
  globalThis.document = { getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] };
  try {
    assert.doesNotThrow(() => openEnvironmentSpawn({ dataset: {} }, () => {}, () => {}));
  } finally {
    if (had) globalThis.document = prev; else delete globalThis.document;
  }
});
