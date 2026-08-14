// Run and diagnostics data helpers, tested by CALLING them.
//
// All four lived in app.js and were unreachable. Three of them decide something whose failure a caller
// cannot see: an id that resolves to nothing, a query string missing its filter, an option list that
// silently stops updating.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import { patchRun, runQueryPath, runSourceMessage, syncRunFilterOptions } from "./run-helpers.mjs";

/** Seal the shared `state` fields these read. */
function withState(fields, run) {
  const saved = {};
  for (const k of Object.keys(fields)) saved[k] = state[k];
  Object.assign(state, fields);
  try {
    return run();
  } finally {
    Object.assign(state, saved);
  }
}

// --- runQueryPath ------------------------------------------------------------------------------

test("runQueryPath always caps the page, and adds status only when there is one", () => {
  // The limit is not optional: without it the endpoint returns every run the service has ever recorded,
  // which is the difference between a dashboard poll and a stall.
  withState({ runStatusFilter: "" }, () => {
    const all = runQueryPath();
    assert.match(all, /limit=80/);
    assert.doesNotMatch(all, /status=/, "an empty filter must not send status=");

    const failed = runQueryPath("failed");
    assert.match(failed, /limit=80/);
    assert.match(failed, /status=failed/);
  });
});

test("runQueryPath defaults to the CURRENT filter, so a caller cannot silently widen it", () => {
  // The default parameter is `state.runStatusFilter`. Defaulting to "" instead would make every
  // no-argument call quietly fetch all statuses while the UI still showed a filter.
  withState({ runStatusFilter: "queued" }, () => {
    assert.match(runQueryPath(), /status=queued/);
  });
});

test("a status with URL-significant characters is ENCODED", () => {
  withState({ runStatusFilter: "" }, () => {
    assert.match(runQueryPath("a b&c"), /status=a\+b%26c|status=a%20b%26c/);
  });
});

// --- runSourceMessage --------------------------------------------------------------------------

const MSGS = [{ id: "m1", subject: "one" }, { id: "m2", subject: "two" }];

test("runSourceMessage prefers the run's own id, in camel then snake case", () => {
  // The API has returned both spellings. Reading only one means the source message silently fails to
  // resolve for half the runs, and the inspector shows no origin at all.
  withState({ messages: MSGS, inspector: { sourceMessageId: "" } }, () => {
    assert.equal(runSourceMessage({ messageId: "m1" })?.id, "m1");
    assert.equal(runSourceMessage({ message_id: "m2" })?.id, "m2");
  });
});

test("it falls back to the INSPECTOR's source id when the run carries none", () => {
  withState({ messages: MSGS, inspector: { sourceMessageId: "m2" } }, () => {
    assert.equal(runSourceMessage({})?.id, "m2");
    assert.equal(runSourceMessage(undefined)?.id, "m2", "a missing run must not throw");
  });
});

test("an id that matches nothing returns NULL rather than undefined", () => {
  // Callers branch on the result. `undefined` and `null` behave the same in a truthiness test, but the
  // explicit null is what says "looked and did not find" rather than "never ran".
  withState({ messages: MSGS, inspector: { sourceMessageId: "" } }, () => {
    assert.equal(runSourceMessage({ messageId: "nope" }), null);
    assert.equal(runSourceMessage({}), null, "no id anywhere is also null");
  });
});

test("A WHITESPACE-ONLY id MASKS the inspector fallback — asserted as it behaves, not as I assumed", () => {
  // I expected blank to fall through to `state.inspector.sourceMessageId`. It does not, and the reason is
  // the order of operations: `String(a || b || c).trim()` picks the first TRUTHY value and trims only the
  // winner, and "   " is truthy. So a run carrying a blank messageId resolves to null even when the
  // inspector knows the answer.
  //
  // Pinned as current behaviour rather than quietly "fixed": trimming each candidate before the `||`
  // chain would change which message the inspector shows for real runs, and that is a product decision,
  // not a tidy-up. This test is what makes the choice visible if anyone reconsiders.
  withState({ messages: MSGS, inspector: { sourceMessageId: "m1" } }, () => {
    assert.equal(runSourceMessage({ messageId: "   " }), null, "blank wins the || chain and trims to empty");
    assert.equal(runSourceMessage({ messageId: "" })?.id, "m1", "…while a truly EMPTY id does fall through");
  });
});

// --- syncRunFilterOptions ----------------------------------------------------------------------

/** A <select> double that records how many times its markup was rebuilt. */
function select() {
  return { dataset: {}, value: "", innerHTML: "", rebuilds: 0 };
}

function withSelect(el, run) {
  const had = "document" in globalThis;
  const prev = globalThis.document;
  globalThis.document = { getElementById: (id) => (id === "sel" ? el : null) };
  const proxy = new Proxy(el, {
    set(t, k, v) { if (k === "innerHTML") t.rebuilds += 1; t[k] = v; return true; },
  });
  globalThis.document = { getElementById: (id) => (id === "sel" ? proxy : null) };
  try {
    return run();
  } finally {
    if (had) globalThis.document = prev; else delete globalThis.document;
  }
}

test("options are deduped, sorted, and led by an 'Any' blank", () => {
  const el = select();
  withSelect(el, () => syncRunFilterOptions("sel", ["b", "a", "b", "", null], ""));
  assert.match(el.innerHTML, /Any/);
  assert.ok(el.innerHTML.indexOf(">a<") < el.innerHTML.indexOf(">b<"), "sorted");
  assert.equal((el.innerHTML.match(/>b</g) || []).length, 1, "deduped");
});

test("AN UNCHANGED OPTION SET IS NOT REBUILT — but the value is still applied", () => {
  // `dataset.optsSig`. The filters re-sync on every poll; rebuilding the markup each time would destroy
  // and recreate the options under an operator who has the dropdown OPEN. The `return` after setting
  // `sel.value` is what keeps the selection correct without the rebuild — dropping it would leave the
  // control showing a stale choice forever.
  const el = select();
  withSelect(el, () => {
    syncRunFilterOptions("sel", ["a", "b"], "a");
    const first = el.rebuilds;
    syncRunFilterOptions("sel", ["a", "b"], "b");
    assert.equal(el.rebuilds, first, "same options must not rebuild");
    assert.equal(el.value, "b", "…but the selection must still follow");
  });
});

test("a CHANGED option set does rebuild", () => {
  const el = select();
  withSelect(el, () => {
    syncRunFilterOptions("sel", ["a"], "");
    const first = el.rebuilds;
    syncRunFilterOptions("sel", ["a", "c"], "");
    assert.ok(el.rebuilds > first, "new options must reach the DOM");
  });
});

test("a missing select is a silent no-op", () => {
  // The filters live on one page; this runs on every refresh regardless of which page is open.
  const had = "document" in globalThis;
  const prev = globalThis.document;
  globalThis.document = { getElementById: () => null };
  try {
    assert.doesNotThrow(() => syncRunFilterOptions("absent", ["a"], ""));
  } finally {
    if (had) globalThis.document = prev; else delete globalThis.document;
  }
});

test("option values are ESCAPED into the markup", () => {
  // The values come from run data, not from a fixed list. An unescaped quote would break out of the
  // attribute and silently truncate the option list.
  const el = select();
  withSelect(el, () => syncRunFilterOptions("sel", ['a"><script>'], ""));
  assert.doesNotMatch(el.innerHTML, /<script>/, "no raw markup may reach the DOM");
});

// --- patchRun ----------------------------------------------------------------------------------

test("patchRun PATCHes the encoded run id with a JSON body", () => {
  const calls = [];
  const had = "fetch" in globalThis;
  const prev = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), method: init?.method, body: init?.body });
    return {
      ok: true, status: 200, headers: { get: () => "application/json" },
      json: async () => ({}), text: async () => "{}",
    };
  };
  return patchRun("run/1 2", { status: "cancelled" })
    .then(() => {
      assert.equal(calls.length, 1);
      assert.match(calls[0].url, /\/dispatch\/runs\/run%2F1(%20|\+)2$/, "the id is encoded into the path");
      assert.equal(calls[0].method, "PATCH");
      assert.deepEqual(JSON.parse(calls[0].body), { status: "cancelled" });
    })
    .finally(() => {
      if (had) globalThis.fetch = prev; else delete globalThis.fetch;
    });
});
