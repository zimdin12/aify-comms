// Real tests for the Work Loop overview panels.
//
// `activityItems` merges three feeds, caps each, sorts by time and caps again — four decisions, none of
// which had a test while this lived in app.js, because that file is reachable only by source regex.
// `CONTRACT_BOARD_COLUMNS` carries six predicates that decide which column a contract lands in; a wrong
// one silently files work under the wrong heading, which is exactly the failure the board exists to
// prevent.
//
// SEALING. `state` is a shared singleton, so every field read here is rebuilt per test. `document` does not
// exist in Node and is installed only for the tests that render, then removed.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import {
  CONTRACT_BOARD_COLUMNS,
  activityItems,
  applyContractView,
  applyWorkView,
  diagnosticKey,
  filtered,
  jumpFromDiagnostic,
  renderContractBoard,
  toggleDiagnosticSelection,
} from "./work-loop-panels.mjs";

function seed({ filter = "", runs = [], messages = [], contracts = [] } = {}) {
  state.filter = filter;
  state.runs = runs;
  state.messages = messages;
  state.contracts = contracts;
}

test("filtered returns everything when the search box is empty or whitespace", () => {
  const items = [{ a: "one" }, { a: "two" }];
  for (const noise of ["", "   ", "\t"]) {
    seed({ filter: noise });
    assert.equal(filtered(items, ["a"]), items, "an empty needle must return the SAME array, not a copy");
  }
});

test("filtered matches case-insensitively across any of the named fields", () => {
  const items = [
    { id: "alpha", subject: "Deploy" },
    { id: "beta", subject: "Refactor" },
  ];
  seed({ filter: "ALPHA" });
  assert.deepEqual(filtered(items, ["id", "subject"]).map((i) => i.id), ["alpha"]);

  seed({ filter: "  refactor  " });
  assert.deepEqual(filtered(items, ["id", "subject"]).map((i) => i.id), ["beta"],
    "the needle is trimmed — a trailing space from the search box must not stop it matching");

  seed({ filter: "alpha" });
  assert.deepEqual(filtered(items, ["subject"]).map((i) => i.id), [],
    "a field not named must not be searched");
});

test("filtered tolerates missing and non-string fields", () => {
  seed({ filter: "5" });
  const items = [{ id: 5 }, { id: null }, {}];
  assert.deepEqual(filtered(items, ["id"]), [{ id: 5 }], "a number must be coerced, a null must not throw");
});

test("diagnosticKey is a stable kind:id pair", () => {
  assert.equal(diagnosticKey("run", "r1"), "run:r1");
  assert.notEqual(diagnosticKey("run", "1"), diagnosticKey("r", "un1"),
    "the separator must keep two different pairs distinct");
});

const at = (iso) => ({ startedAt: iso, requestedAt: iso, createdAt: iso });

test("activityItems merges runs, messages and contracts, newest first", () => {
  seed({
    runs: [{ id: "r1", subject: "run one", ...at("2026-08-14T10:00:00Z") }],
    messages: [{ id: "m1", subject: "msg one", ...at("2026-08-14T12:00:00Z") }],
    contracts: [{ id: "c1", subject: "contract one", ...at("2026-08-14T11:00:00Z") }],
  });
  const items = activityItems();
  assert.deepEqual(items.map((i) => i.kind), ["message", "contract", "run"],
    "the three feeds interleave by time, they are not concatenated by kind");
});

test("activityItems takes at most 8 from each feed and 10 overall", () => {
  // Both caps matter: without the per-feed cap a busy run list would crowd out every message; without the
  // overall cap the panel grows without bound.
  const many = (prefix, n) => Array.from({ length: n }, (_, i) =>
    ({ id: `${prefix}${i}`, subject: `${prefix}${i}`, ...at(`2026-08-14T10:00:${String(i).padStart(2, "0")}Z`) }));
  seed({ runs: many("r", 20), messages: many("m", 20), contracts: many("c", 20) });
  const items = activityItems();
  assert.equal(items.length, 10, "the merged feed is capped at 10");

  seed({ runs: many("r", 20), messages: [], contracts: [] });
  assert.equal(activityItems().length, 8, "…and each source contributes at most 8");
});

test("activityItems gives an untitled record a fallback title rather than blank", () => {
  seed({ runs: [{ id: "r1", ...at("2026-08-14T10:00:00Z") }], messages: [{ id: "m1" }] });
  const items = activityItems();
  assert.equal(items.find((i) => i.kind === "run").title, "r1", "a run falls back to its id");
  assert.equal(items.find((i) => i.kind === "message").title, "(no subject)",
    "a message with neither subject nor body says so instead of rendering empty");
});

test("an unread message reads as queued and a read one as completed", () => {
  seed({ messages: [{ id: "m1", read: false }, { id: "m2", read: true }] });
  const byId = Object.fromEntries(activityItems().map((i) => [i.id, i.status]));
  assert.equal(byId.m1, "queued");
  assert.equal(byId.m2, "completed");
});

test("an overdue contract reads as failed regardless of its state", () => {
  seed({ contracts: [{ id: "c1", state: "working", overdue: true }] });
  assert.equal(activityItems()[0].status, "failed",
    "overdue is the headline — a contract quietly working past its deadline is the thing to surface");
});

test("the board columns sort contracts into the expected headings", () => {
  const col = (key) => CONTRACT_BOARD_COLUMNS.find((c) => c.key === key);
  assert.equal(col("overdue").match({ overdue: true }), true);
  assert.equal(col("working").match({ state: "working" }), true);
  assert.equal(col("queued").match({ state: "queued" }), true);
  for (const s of ["sent", "seen", "missing_reply"]) {
    assert.equal(col("awaiting").match({ state: s }), true, `"${s}" belongs under Awaiting`);
  }
  for (const s of ["answered", "closed"]) {
    assert.equal(col("answered").match({ state: s }), true, `"${s}" belongs under Answered`);
  }
  assert.equal(col("failed").match({ state: "failed" }), true);
  assert.equal(col("working").match({ state: "queued" }), false, "the predicates must not overlap on state");
});

test("only the always-on columns are permanent; the terminal ones are not", () => {
  // Answered and Failed are hidden when empty — a board permanently showing two empty terminal columns
  // wastes the width the live ones need.
  const always = CONTRACT_BOARD_COLUMNS.filter((c) => c.always).map((c) => c.key);
  assert.deepEqual(always, ["overdue", "working", "queued", "awaiting"]);
});

test("renderContractBoard RETURNS markup and always shows the live columns", () => {
  // It returns a string; it does not write to the DOM. My first version of this test asserted against a
  // fake host element and failed — the test was wrong, not the code.
  seed({});
  const html = renderContractBoard([{ id: "c1", subject: "one", state: "working" }]);
  assert.ok(html.includes("Working"), "the column the contract belongs to must render");
  assert.ok(html.includes("Overdue"), "an always-on column renders even when empty");
  assert.ok(html.includes("board-col-empty"), "…and says so rather than rendering a blank gap");
  assert.ok(!html.includes("Answered"), "a terminal column with nothing in it is hidden");
});

test("a contract with an unrecognised state lands in Other rather than vanishing", () => {
  // Forward-compat: the server can grow a new state before the dashboard knows about it. Dropping such a
  // contract silently would hide live work from the board that exists to show it.
  seed({});
  const html = renderContractBoard([{ id: "c9", subject: "from the future", state: "quantum" }]);
  assert.ok(html.includes("Other"), "an unknown state must still be surfaced");
  assert.ok(html.includes("from the future"));
});

// --- the two diagnostics view controls ----------------------------------------------------------
//
// Both were branch bodies inside app.js's delegated click handler, so neither was reachable from a test.

/** A DOM stub recording attribute writes, with `n` work-view buttons and named selects. */
function viewDom({ views = [], selects = {}, grid = true } = {}) {
  const buttons = views.map((v) => ({
    dataset: { workView: v },
    active: null,
    pressed: null,
    classList: { toggle(_c, on) { this._o.active = on; } },
    setAttribute(_k, val) { this.pressed = val; },
  }));
  for (const b of buttons) b.classList._o = b;

  const gridEl = grid ? { attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } } : null;
  const selEls = {};
  for (const [id, ok] of Object.entries(selects)) {
    if (ok) selEls[id] = { value: "", events: [], dispatchEvent(e) { this.events.push(e?.type ?? "?"); } };
  }
  return {
    buttons,
    gridEl,
    selEls,
    doc: {
      querySelector: (sel) => (sel === ".diagnostics-grid" ? gridEl : null),
      querySelectorAll: (sel) => (sel.includes("data-work-view") ? buttons : []),
      getElementById: (id) => selEls[id] ?? null,
    },
  };
}

function withViewDom(dom, run) {
  const hadDoc = "document" in globalThis;
  const hadEvt = "Event" in globalThis;
  const hadLs = "localStorage" in globalThis;
  const prevDoc = globalThis.document;
  const store = new Map();
  globalThis.document = dom.doc;
  globalThis.localStorage = { setItem: (k, v) => store.set(k, v), getItem: (k) => store.get(k) ?? null };
  if (!hadEvt) globalThis.Event = class { constructor(type, opts) { this.type = type; Object.assign(this, opts); } };
  try {
    run(store);
  } finally {
    if (hadDoc) globalThis.document = prevDoc; else delete globalThis.document;
    if (!hadLs) delete globalThis.localStorage;
    if (!hadEvt) delete globalThis.Event;
  }
}

test("applyWorkView sets the grid layout, presses exactly one button, and persists the choice", () => {
  // Three effects, and each is separately droppable. Persisting without pressing leaves the UI showing
  // the old view; pressing without persisting looks correct until a reload.
  const dom = viewDom({ views: ["list", "board"] });
  withViewDom(dom, (store) => {
    applyWorkView({ dataset: { workView: "board" } });
    assert.equal(dom.gridEl.attrs["data-work-view"], "board", "the grid follows the button");
    assert.deepEqual(dom.buttons.map((b) => b.active), [false, true], "exactly one is active");
    assert.deepEqual(dom.buttons.map((b) => b.pressed), ["false", "true"], "aria-pressed mirrors it");
    assert.equal(store.get("aifyWorkView"), "board");
  });
});

test("applyWorkView survives a missing grid — the panel may not be rendered", () => {
  // `if (grid)`. The handler fires from anywhere in the dashboard; throwing here would kill every
  // branch after it.
  const dom = viewDom({ views: ["list"], grid: false });
  withViewDom(dom, (store) => {
    assert.doesNotThrow(() => applyWorkView({ dataset: { workView: "list" } }));
    assert.equal(store.get("aifyWorkView"), "list", "the choice is still persisted");
  });
});

test("applyWorkView still presses buttons when storage REFUSES", () => {
  // `try { … } catch { /* private mode */ }`. In private mode the write throws, and the visible part of
  // the toggle must still happen — otherwise the button appears dead.
  const dom = viewDom({ views: ["list", "board"] });
  const hadLs = "localStorage" in globalThis;
  withViewDom(dom, () => {
    globalThis.localStorage = { setItem() { throw new Error("private mode"); } };
    assert.doesNotThrow(() => applyWorkView({ dataset: { workView: "board" } }));
    assert.deepEqual(dom.buttons.map((b) => b.active), [false, true]);
  });
  if (!hadLs) delete globalThis.localStorage;
});

test("jumpFromDiagnostic routes a `run:` key to the RUN filter and anything else to the contract state", () => {
  // The prefix IS the routing. Sending both to one select would silently filter the wrong panel — and
  // `slice(4)` must strip exactly `run:`, or the filter is set to a value no option has.
  const dom = viewDom({ selects: { "run-status-filter": true, "contract-state": true } });
  withViewDom(dom, () => {
    jumpFromDiagnostic({ dataset: { diagJump: "run:failed" } });
    assert.equal(dom.selEls["run-status-filter"].value, "failed", "the `run:` prefix is stripped");
    assert.deepEqual(dom.selEls["run-status-filter"].events, ["change"], "and change is dispatched");
    assert.equal(dom.selEls["contract-state"].value, "", "the other select is untouched");

    jumpFromDiagnostic({ dataset: { diagJump: "open" } });
    assert.equal(dom.selEls["contract-state"].value, "open");
    assert.deepEqual(dom.selEls["contract-state"].events, ["change"]);
  });
});

test("jumpFromDiagnostic dispatches a BUBBLING change — the listener is delegated", () => {
  // `new Event('change', { bubbles: true })`. Setting `.value` fires nothing on its own, and a
  // non-bubbling event never reaches a delegated listener, so the jump would set the control and
  // change nothing on screen.
  const dom = viewDom({ selects: { "contract-state": true } });
  withViewDom(dom, () => {
    let seen = null;
    dom.selEls["contract-state"].dispatchEvent = (e) => { seen = e; };
    jumpFromDiagnostic({ dataset: { diagJump: "closed" } });
    assert.equal(seen?.type, "change");
    assert.equal(seen?.bubbles, true);
  });
});

test("jumpFromDiagnostic is a no-op when the target select is absent, and handles an empty key", () => {
  const dom = viewDom({ selects: {} });
  withViewDom(dom, () => {
    assert.doesNotThrow(() => jumpFromDiagnostic({ dataset: { diagJump: "run:failed" } }));
    assert.doesNotThrow(() => jumpFromDiagnostic({ dataset: {} }), "an absent key must not throw");
  });
});

// --- toggleDiagnosticSelection -----------------------------------------------------------------

/** Seal the diagnostics selection Set. */
function withDiagnostics(selected, run) {
  const saved = state.selectedDiagnosticIds;
  state.selectedDiagnosticIds = new Set(selected);
  try {
    return run();
  } finally {
    state.selectedDiagnosticIds = saved;
  }
}

test("THE SELECTION IS KEYED BY KIND+ID, so a run and a contract sharing an id cannot collide", () => {
  // `diagnosticKey(kind, id)`. Runs and contracts are selected into ONE Set; keying on the bare id would
  // make selecting run "7" also select contract "7", and a bulk action would then hit a record the
  // operator never ticked.
  withDiagnostics([], () => {
    toggleDiagnosticSelection({ checked: true, dataset: { diagnosticKind: "run", diagnosticSelect: "7" } }, () => {});
    toggleDiagnosticSelection({ checked: true, dataset: { diagnosticKind: "contract", diagnosticSelect: "7" } }, () => {});
    assert.equal(state.selectedDiagnosticIds.size, 2, "two distinct keys, not one");
    assert.ok(state.selectedDiagnosticIds.has(diagnosticKey("run", "7")));
    assert.ok(state.selectedDiagnosticIds.has(diagnosticKey("contract", "7")));
  });
});

test("the kind DEFAULTS to 'run' when the element does not declare one", () => {
  // `dataset.diagnosticKind || 'run'`. An undefined kind would key as "undefined:7" and never match the
  // key the bulk toolbar builds, so the row would appear selected and be silently skipped.
  withDiagnostics([], () => {
    toggleDiagnosticSelection({ checked: true, dataset: { diagnosticSelect: "7" } }, () => {});
    assert.ok(state.selectedDiagnosticIds.has(diagnosticKey("run", "7")));
  });
});

test("it mirrors the checkbox rather than flipping, and refreshes the bulk toolbar each time", () => {
  // Same reasoning as the session checkbox: the browser has already applied the check. The toolbar shows
  // the count and the actions, so it must be redrawn on every change or it acts on a stale selection.
  withDiagnostics([], () => {
    let toolbars = 0;
    const el = { checked: true, dataset: { diagnosticKind: "run", diagnosticSelect: "7" } };
    toggleDiagnosticSelection(el, () => { toolbars += 1; });
    toggleDiagnosticSelection(el, () => { toolbars += 1; });
    assert.equal(state.selectedDiagnosticIds.size, 1, "re-affirming a checked box keeps exactly one");

    toggleDiagnosticSelection({ ...el, checked: false }, () => { toolbars += 1; });
    assert.equal(state.selectedDiagnosticIds.size, 0);
    assert.equal(toolbars, 3, "every change redraws the toolbar");
  });
});

// --- applyContractView -------------------------------------------------------------------------

test("applyContractView normalises to exactly two states and never a third", () => {
  // `=== 'board' ? 'board' : 'list'`. The value drives which panel renders; a stray attribute passed
  // through verbatim would render neither, leaving the Work Loop page blank.
  const saved = state.contractView;
  const hadLs = "localStorage" in globalThis;
  const prev = globalThis.localStorage;
  const store = new Map();
  globalThis.localStorage = { setItem: (k, v) => store.set(k, v), getItem: (k) => store.get(k) ?? null };
  try {
    for (const [raw, expected] of [["board", "board"], ["list", "list"], ["Board", "list"], [undefined, "list"], ["wat", "list"]]) {
      let renders = 0;
      applyContractView({ dataset: { contractView: raw } }, () => { renders += 1; });
      assert.equal(state.contractView, expected, `${JSON.stringify(raw)} → ${expected}`);
      assert.equal(store.get("aifyContractView"), expected, "…and the same value is persisted");
      assert.equal(renders, 1, "every click re-renders");
    }
  } finally {
    state.contractView = saved;
    if (hadLs) globalThis.localStorage = prev; else delete globalThis.localStorage;
  }
});

test("applyContractView still switches the view when storage REFUSES", () => {
  // `try { … } catch { /* private mode */ }`. The state assignment precedes the write and the render
  // follows it, so an unguarded throw would leave the layout changed and never drawn.
  const saved = state.contractView;
  const hadLs = "localStorage" in globalThis;
  const prev = globalThis.localStorage;
  globalThis.localStorage = { setItem() { throw new Error("private mode"); } };
  try {
    let renders = 0;
    assert.doesNotThrow(() => applyContractView({ dataset: { contractView: "board" } }, () => { renders += 1; }));
    assert.equal(state.contractView, "board");
    assert.equal(renders, 1, "the render must still happen");
  } finally {
    state.contractView = saved;
    if (hadLs) globalThis.localStorage = prev; else delete globalThis.localStorage;
  }
});
