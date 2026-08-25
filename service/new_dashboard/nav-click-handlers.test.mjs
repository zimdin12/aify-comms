// Navigation and analytics-range clicks, tested by CALLING them.
//
// All three were branch bodies inside app.js's delegated click handler, so nothing could reach them.
// Every one pairs a navigation with a side effect that must happen in the SAME click — and half of any
// of these looks exactly like a working button: the page changes, and the thing the page is for never
// loads.

import assert from "node:assert/strict";
import test from "node:test";

import { rangeDef } from "./analytics.js";
import { setApiBase } from "./api-client.mjs";
import { state } from "./state.mjs";
import {
  navigateToPage,
  openEnvironmentSpawn,
  openHermesTabFromRow,
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

// --- openHermesTabFromRow ----------------------------------------------------------------------

/** Capture window.open calls. */
function withWindowOpen(run) {
  const had = "window" in globalThis;
  const prev = globalThis.window;
  const calls = [];
  globalThis.window = { open: (...a) => { calls.push(a); return null; } };
  try {
    return run(calls);
  } finally {
    if (had) globalThis.window = prev; else delete globalThis.window;
  }
}

test("openHermesTabFromRow opens with noopener AND noreferrer", () => {
  // Without `noopener` the opened page gets a live handle on this one through `window.opener` and can
  // navigate the dashboard out from under the operator. It is one string and it is the entire security
  // property of this two-line function.
  withWindowOpen((calls) => {
    openHermesTabFromRow({ dataset: { url: "http://gw.local:8080/" } });
    assert.equal(calls.length, 1);
    const [url, target, features] = calls[0];
    assert.equal(url, "http://gw.local:8080/");
    assert.equal(target, "_blank");
    assert.match(features, /noopener/);
    assert.match(features, /noreferrer/);
  });
});

test("a row with NO url opens nothing rather than a blank tab", () => {
  // `if (url)`. Opening `undefined` yields an about:blank tab that the operator has to close, and on a
  // row whose data is still loading that would happen on every click.
  withWindowOpen((calls) => {
    openHermesTabFromRow({ dataset: {} });
    openHermesTabFromRow({ dataset: { url: "" } });
    assert.deepEqual(calls, []);
  });
});

/**
 * Navigate with a stubbed document and fetch, recording the paths that went out.
 *
 * The document answers every lookup with null on purpose: `renderFiles` returns early on a missing
 * host, so this exercises the navigation's fetch without pulling a DOM into the test.
 */
async function navigateWithStubs(page) {
  const saved = { document: globalThis.document, fetch: globalThis.fetch, files: state.files };
  const requested = [];
  globalThis.document = { getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] };
  globalThis.fetch = async (url) => {
    requested.push(String(url));
    return { ok: true, status: 200, statusText: "OK", text: async () => JSON.stringify({ files: [] }) };
  };
  setApiBase("");
  try {
    navigateToPage(page, () => {}, () => {});
    // The fetch goes out synchronously, but the .then(renderFiles) does not; drain it so an error there
    // surfaces as a failure here rather than as an unhandled rejection in a later test.
    await new Promise((r) => setTimeout(r, 0));
  } finally {
    globalThis.document = saved.document;
    globalThis.fetch = saved.fetch;
    state.files = saved.files;
  }
  return requested;
}

test("opening the Files page fetches the list it is about to show", async () => {
  // The poll skips /shared while the page is closed (files-page.mjs), so this navigation is the ONLY
  // thing that fills the page. Without it the Files page shows the last thing seen, or nothing at all
  // on a first open, for up to a full 15s refresh interval -- a page that loads nothing looks exactly
  // like a working button.
  const requested = await navigateWithStubs("files");
  assert.equal(requested.filter((u) => u.includes("/shared")).length, 1, "opened Files and fetched nothing");
});

test("opening a different page fetches no file list", async () => {
  // The saving depends on this half: a load-on-open that fires on every page would put the poll back.
  const requested = await navigateWithStubs("chat");
  assert.equal(requested.filter((u) => u.includes("/shared")).length, 0, "fetched files for another page");
});
