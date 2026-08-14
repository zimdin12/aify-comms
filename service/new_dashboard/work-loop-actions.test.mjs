// The Work Loop's actions, tested by CALLING them.
//
// The bulk diagnostic action is the most dangerous control on the page: it is the only place one click
// acts on a set the operator did not enumerate item by item. So most of what is asserted here is about
// the SELECTION — that a mixed selection does not quietly do the wrong thing to half of it, that
// nothing is cleared when the confirmation is declined, and that an unsupported combination says so
// rather than silently succeeding on zero items.

import assert from "node:assert/strict";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { state } from "./state.mjs";
import {
  closeWorkContract,
  initWorkLoopActions,
  loadContractsForState,
  remindWorkContract,
  renderContracts,
  renderDiagnosticsBulkToolbar,
  requestBulkDiagnosticAction,
  runMaintenance,
} from "./work-loop-actions.mjs";

function makeEl(extra = {}) {
  return {
    innerHTML: "", textContent: "", className: "", value: "", hidden: false, disabled: false,
    dataset: {}, style: {}, children: [], firstElementChild: null,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    querySelector: () => null, querySelectorAll: () => [], appendChild() {}, setAttribute() {},
    addEventListener() {}, removeEventListener() {}, remove() {}, focus() {},
    ...extra,
  };
}

function makeDialog(answer) {
  const listeners = new Map();
  const button = (key) => makeEl({ addEventListener: (ev, fn) => { if (ev === "click") listeners.set(key, fn); } });
  const confirmBtn = button("confirm");
  const cancelBtn = button("cancel");
  const overlay = makeEl({
    querySelector: (sel) => ({ ".dialog-confirm": confirmBtn, ".dialog-cancel": cancelBtn, ".dialog-input": null }[sel] ?? null),
    querySelectorAll: () => [cancelBtn, confirmBtn],
  });
  overlay.__answer = () => { const fn = listeners.get(answer ? "confirm" : "cancel"); if (fn) fn(); };
  return overlay;
}

function withWorkLoop({ confirm = true } = {}) {
  const els = new Map();
  const sent = [];
  const toasts = [];
  const saved = { document: globalThis.document, fetch: globalThis.fetch, requestAnimationFrame: globalThis.requestAnimationFrame };
  globalThis.requestAnimationFrame = (fn) => { fn(); return 1; };
  globalThis.document = {
    getElementById: (id) => { if (!els.has(id)) els.set(id, makeEl()); return els.get(id); },
    querySelector: () => null, querySelectorAll: () => [],
    createElement: () => makeDialog(confirm),
    addEventListener() {}, removeEventListener() {},
    body: {
      appendChild: (el) => { if (el && el.className === "dialog-overlay") queueMicrotask(() => el.__answer()); },
      classList: { add() {}, remove() {} }, style: { setProperty() {} },
    },
    activeElement: null,
  };
  globalThis.fetch = async (url, options = {}) => {
    const method = (options.method || "GET").toUpperCase();
    if (method !== "GET") sent.push({ url: String(url), method, body: options.body });
    const payload = { ok: true, contracts: [], runs: [], repaired: 3 };
    return { ok: true, status: 200, statusText: "OK", json: async () => payload, text: async () => JSON.stringify(payload) };
  };
  setApiBase("");
  let refreshes = 0;
  initWorkLoopActions({ refresh: async () => { refreshes += 1; } });
  Object.assign(state, {
    contracts: [], contractsBase: [], runs: [], filter: "", contractView: "list",
    selectedDiagnosticIds: new Set(),
  });
  return { els, sent, toasts, restore: () => Object.assign(globalThis, saved), refreshes: () => refreshes };
}

/** Seed a selection the bulk action will see. `selectedDiagnostics()` reads it out of state. */
function select(kinds) {
  state.contracts = kinds.filter((k) => k.kind === "contract").map((k) => ({ id: k.id, runId: k.id, status: "open" }));
  state.runs = kinds.filter((k) => k.kind === "run").map((k) => ({ id: k.id, status: "failed" }));
  state.selectedDiagnosticIds = new Set(kinds.map((k) => `${k.kind}:${k.id}`));
}

const mutating = (h) => h.sent.map((r) => `${r.method} ${r.url}`);

test("an EMPTY selection is a no-op, not an all-items action", async () => {
  // The failure mode a bulk control must never have.
  const h = withWorkLoop({ confirm: true });
  try {
    select([]);
    for (const action of ["close", "remind", "inspect", "clear"]) {
      await requestBulkDiagnosticAction(action);
    }
    assert.deepEqual(mutating(h), []);
  } finally { h.restore(); }
});

test("an unknown action does nothing even with a selection", async () => {
  const h = withWorkLoop({ confirm: true });
  try {
    select([{ kind: "run", id: "r1" }]);
    await requestBulkDiagnosticAction("not-an-action");
    await requestBulkDiagnosticAction("");
    assert.deepEqual(mutating(h), []);
  } finally { h.restore(); }
});

test("BULK CLOSE IS CONFIRMED, AND DECLINING CLEARS NOTHING", async () => {
  // Declining must leave the selection intact — clearing it would make the operator re-select a
  // screenful of items to answer the question again, and is the kind of thing that gets a
  // confirmation clicked through by habit.
  const h = withWorkLoop({ confirm: false });
  try {
    select([{ kind: "run", id: "r1" }, { kind: "contract", id: "c1" }]);
    await requestBulkDiagnosticAction("close");
    assert.deepEqual(mutating(h), []);
    assert.equal(state.selectedDiagnosticIds.size, 2, "a declined bulk action must not clear the selection");
  } finally { h.restore(); }
});

test("bulk close acts on EVERY selected item and clears afterwards", async () => {
  const h = withWorkLoop({ confirm: true });
  try {
    select([{ kind: "run", id: "r1" }, { kind: "run", id: "r2" }]);
    await requestBulkDiagnosticAction("close");
    assert.equal(h.sent.length, 2, "both runs must be closed, not just the first");
    assert.equal(state.selectedDiagnosticIds.size, 0);
    assert.equal(h.refreshes(), 1);
  } finally { h.restore(); }
});

test("bulk close routes CONTRACTS and RUNS differently — they are not the same endpoint", async () => {
  // A contract is closed through its own path; a run is patched. Sending one as the other silently
  // does nothing to half a mixed selection while the toolbar reports success.
  const h = withWorkLoop({ confirm: true });
  try {
    select([{ kind: "contract", id: "c1" }, { kind: "run", id: "r1" }]);
    await requestBulkDiagnosticAction("close");
    assert.equal(h.sent.length, 2, "both kinds must be acted on");
    const urls = h.sent.map((r) => r.url).join(" ");
    assert.match(urls, /c1/);
    assert.match(urls, /r1/);
  } finally { h.restore(); }
});

test("A RUNS-ONLY SELECTION CANNOT BE REMINDED, AND IS TOLD SO", async () => {
  // Only reply-contracts can be reminded. Silently dropping the selection would report success on
  // zero items — which reads to the operator as "reminders sent".
  const h = withWorkLoop({ confirm: true });
  try {
    select([{ kind: "run", id: "r1" }, { kind: "run", id: "r2" }]);
    await requestBulkDiagnosticAction("remind");
    assert.deepEqual(mutating(h), [], "no reminder may be sent");
    assert.equal(state.selectedDiagnosticIds.size, 2, "…and the selection must survive so it can be fixed");
  } finally { h.restore(); }
});

test("a MIXED selection reminds only the contracts", async () => {
  const h = withWorkLoop({ confirm: true });
  try {
    select([{ kind: "run", id: "r1" }, { kind: "contract", id: "c1" }]);
    await requestBulkDiagnosticAction("remind");
    assert.equal(h.sent.length, 1, "exactly the contract, not the run");
    assert.match(h.sent[0].url, /c1/);
  } finally { h.restore(); }
});

test("clear empties the selection WITHOUT sending anything", async () => {
  const h = withWorkLoop({ confirm: true });
  try {
    select([{ kind: "run", id: "r1" }]);
    await requestBulkDiagnosticAction("clear");
    assert.equal(state.selectedDiagnosticIds.size, 0);
    assert.deepEqual(mutating(h), [], "clearing a selection is a local act");
  } finally { h.restore(); }
});

test("inspect opens the FIRST item and leaves the selection alone", async () => {
  const h = withWorkLoop({ confirm: true });
  try {
    select([{ kind: "run", id: "r1" }, { kind: "run", id: "r2" }]);
    await requestBulkDiagnosticAction("inspect");
    assert.equal(state.selectedDiagnosticIds.size, 2, "inspecting is not consuming");
  } finally { h.restore(); }
});

test("the bulk toolbar HIDES itself on an empty selection", async () => {
  const h = withWorkLoop();
  try {
    select([]);
    renderDiagnosticsBulkToolbar();
    const toolbar = h.els.get("diagnostics-bulk-toolbar");
    assert.equal(toolbar.hidden, true);
    assert.equal(toolbar.innerHTML, "", "…and leaves no stale buttons behind it");
  } finally { h.restore(); }
});

test("the bulk toolbar COUNTS the two kinds separately", async () => {
  // The operator is about to act on this set; "3 selected" without the split hides that two of them
  // are runs, which the Remind button cannot touch.
  const h = withWorkLoop();
  try {
    select([{ kind: "contract", id: "c1" }, { kind: "run", id: "r1" }, { kind: "run", id: "r2" }]);
    renderDiagnosticsBulkToolbar();
    const html = h.els.get("diagnostics-bulk-toolbar").innerHTML;
    assert.equal(h.els.get("diagnostics-bulk-toolbar").hidden, false);
    assert.match(html, /3 selected/);
    assert.match(html, /1 work/);
    assert.match(html, /2 runs/);
  } finally { h.restore(); }
});

test("MAINTENANCE IS CONFIRMED, and an unknown action never prompts", async () => {
  const unknown = withWorkLoop({ confirm: true });
  try {
    await runMaintenance("not-a-maintenance-action");
    assert.deepEqual(mutating(unknown), []);
  } finally { unknown.restore(); }

  const declined = withWorkLoop({ confirm: false });
  try {
    await runMaintenance("reconcile-terminals");
    assert.deepEqual(mutating(declined), [], "declining must send nothing");
  } finally { declined.restore(); }
});

test("a failing maintenance run reports rather than rejecting", async () => {
  const h = withWorkLoop({ confirm: true });
  try {
    globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
    await assert.doesNotReject(() => runMaintenance("reconcile-terminals"));
  } finally { h.restore(); }
});

test("closing a work contract can SKIP its confirmation — the flag bulk relies on", async () => {
  // Bulk asks once for the whole set. If the per-item call still prompted, the operator would answer
  // the same question N times and answering No to the fifth would leave four already closed.
  const h = withWorkLoop({ confirm: false });
  try {
    await closeWorkContract("c1", false, false);
    assert.equal(h.sent.length, 1, "an unconfirmed close must go straight out");
    assert.equal(h.refreshes(), 0, "…and refreshAfter=false must not poll per item");
  } finally { h.restore(); }
});

test("remindWorkContract REFUSES a falsy id, like every one of its neighbours", async () => {
  // It used to post `?runId=` and let the server decide, alone among `closeWorkContract`,
  // `stopAgentWorker`, `removeAgent`, `deleteSessionById` and `requestSessionControl`. Pinned as the
  // unguarded behaviour for one commit — the finding surfaced inside a relocation whose bodies were
  // proved byte-identical, so a guard there would have smuggled a behaviour change into a refactor —
  // and this is that assertion flipped.
  for (const id of ["", null, undefined, 0]) {
    const h = withWorkLoop({ confirm: true });
    try {
      await remindWorkContract(id, false);
      assert.deepEqual(mutating(h), [], `${JSON.stringify(id)} must not reach the server`);
    } finally { h.restore(); }
  }
});

test("…and a real id still sends", async () => {
  // The other direction, without which the guard above passes against a function that does nothing.
  const h = withWorkLoop({ confirm: true });
  try {
    await remindWorkContract("c1", false);
    assert.equal(h.sent.length, 1);
    assert.match(h.sent[0].url, /runId=c1/);
  } finally { h.restore(); }
});

test("the BULK remind is unaffected by the guard", async () => {
  // Bulk calls it per contract with `refreshAfter=false`. A guard placed after the api call, or one
  // that returned a rejected promise, would have broken the batch instead of the empty case.
  const h = withWorkLoop({ confirm: true });
  try {
    select([{ kind: "contract", id: "c1" }, { kind: "contract", id: "c2" }]);
    await requestBulkDiagnosticAction("remind");
    assert.equal(h.sent.length, 2, "both contracts must still be reminded");
  } finally { h.restore(); }
});

test("renderContracts NO-OPS without its host element, like its neighbouring renderers", async () => {
  // It used to dereference `host` directly, alone among `renderUsagePools`,
  // `renderDiagnosticsBulkToolbar` and `renderSessionConsole`. That matters more than it looks: it
  // runs from the render orchestrator on EVERY poll, so a renamed `#contract-list` would have stopped
  // the whole dashboard re-rendering rather than just this panel. Pinned as a throw for one commit,
  // for the same reason as the other two findings; this is the assertion flipped.
  const saved = globalThis.document;
  globalThis.document = { getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] };
  try {
    assert.doesNotThrow(() => renderContracts());
  } finally { globalThis.document = saved; }
});

test("…and it still renders when the host IS there", async () => {
  // Without this the guard above passes against a renderer that returns unconditionally.
  const h = withWorkLoop();
  try {
    state.contracts = [];
    renderContracts();
    assert.match(h.els.get("contract-list").innerHTML, /No contracts match/,
      "an empty filter must still paint its empty state");
  } finally { h.restore(); }
});

test("the bulk toolbar is still rendered when the contract host is missing", async () => {
  // The toolbar lives in its own container and prunes the selection as it draws. Returning early
  // without it would leave a stale "N selected" beside a panel that never rendered.
  const saved = globalThis.document;
  const touched = [];
  globalThis.document = {
    getElementById: (id) => { touched.push(id); return null; },
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  try {
    renderContracts();
    assert.ok(touched.includes("diagnostics-bulk-toolbar"),
      "the toolbar renderer must still have been reached");
  } finally { globalThis.document = saved; }
});

test("INIT REFUSES A MISSING refresh", () => {
  assert.throws(() => initWorkLoopActions({}), /refresh/);
  assert.throws(() => initWorkLoopActions(null), TypeError);
  assert.doesNotThrow(() => initWorkLoopActions({ refresh: async () => {} }));
});
