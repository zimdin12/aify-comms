// A drawer opened while another drawer's fetch is in flight keeps refreshing.
//
// THE DEFECT (v0.7.2, external review item 3). The agent, message and identity-directory drawers opened
// with `{ ...state.inspector, kind: ... }`, so they copied `loading: true` from a History or run drawer
// still fetching, and `loadingMore` from a run drawer's "Load more". The earlier fetch then found its
// drawer gone and returned without clearing the flag, and app.js skips refreshing a drawer whose flag
// is set, so the new drawer stopped updating until it was closed.
//
// Driven through the real openers with every fetch held until the test releases it. Only `document`
// and `fetch` are fakes.
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { state, emptyInspector, inspectorOpening } from "./state.mjs";
import { inspectorRefreshDecision } from "./inspector-refresh.mjs";
import { openCompactionHistory, openMessageDetail } from "./inspector-forms.mjs";
import { initRunInspector, openRunInspector, loadMoreRunEvents } from "./run-inspector.mjs";
import { openAgentDrawer } from "./agent-drawer.mjs";
import { openIdentityDirectory } from "./identity-directory.mjs";

function el() {
  const classes = new Set();
  return {
    innerHTML: "", textContent: "", value: "", dataset: {}, style: {}, children: [],
    classList: { add: (x) => classes.add(x), remove: (x) => classes.delete(x), toggle() {}, contains: (x) => classes.has(x) },
    querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, setAttribute() {},
    appendChild: (k) => k, remove() {}, focus() {}, contains: () => false,
  };
}
const els = {};
const pending = [];
const saved = Object.fromEntries(["document", "fetch", "requestAnimationFrame"].map((k) => [k, globalThis[k]]));
test.before(() => {
  globalThis.document = {
    getElementById: (id) => (els[id] ||= el()),
    querySelector: () => null, querySelectorAll: () => [],
    createElement: () => el(), body: el(), activeElement: null,
  };
  globalThis.requestAnimationFrame = (fn) => fn();
  globalThis.fetch = (url) => new Promise((resolve) => {
    pending.push({ url: String(url), release: (body) => resolve(new Response(JSON.stringify(body), { status: 200 })) });
  });
  setApiBase("");
  initRunInspector({ closeInspector() {}, evaluateFlowGates() {}, openInspector() {}, openRunConsole() {}, refresh: async () => {}, renderDiagnosticsBulkToolbar() {} });
  Object.assign(state, { agents: [{ id: "b", status: "online" }], sessions: [], environments: [], runs: [], messages: [{ id: "m1", from: "a", to: "b", body: "hi" }], filter: "" });
});
test.after(() => {
  for (const [k, v] of Object.entries(saved)) { if (v === undefined) delete globalThis[k]; else globalThis[k] = v; }
  state.inspector = emptyInspector();
});

const tick = () => new Promise((resolve) => setTimeout(resolve, 10));
// What app.js asks before it refreshes the open drawer (app.js, refreshOpenInspector).
const decide = () => inspectorRefreshDecision(state.inspector, {
  isOpen: els.inspector.classList.contains("open"),
  isLoading: !!(state.inspector?.loadingMore || state.inspector?.loading),
});
const drain = async (body = {}) => { await tick(); while (pending.length) { pending.shift().release(body); await tick(); } };

const openers = {
  agent: () => openAgentDrawer("b"),
  message: () => openMessageDetail("m1"),
  "identity-directory": () => openIdentityDirectory(),
};

for (const [kind, open] of Object.entries(openers)) {
  test(`the ${kind} drawer opened while History is loading still refreshes`, async () => {
    state.inspector = emptyInspector();
    const history = openCompactionHistory("x");
    await tick();
    open();
    assert.equal(state.inspector.kind, kind);
    await drain({ spawnRequests: [] });
    await history;
    assert.equal(decide(), "refresh", `the ${kind} drawer inherited History's loading flag`);
  });

  test(`the ${kind} drawer opened while a run is loading still refreshes`, async () => {
    state.inspector = emptyInspector();
    const run = openRunInspector({ runId: "r1" });
    await tick();
    open();
    await drain({ run: { id: "r1" }, events: [], hasMore: false });
    await run;
    assert.equal(decide(), "refresh", `the ${kind} drawer inherited the run drawer's loading flag`);
  });
}

test("the agent drawer opened during a run's Load more still refreshes", async () => {
  state.inspector = emptyInspector();
  const run = openRunInspector({ runId: "r1" });
  await drain({ run: { id: "r1" }, events: [{ id: "e1" }], hasMore: true });
  await run;
  assert.equal(decide(), "refresh", "CONTROL: a settled run drawer refreshes");
  const more = loadMoreRunEvents();
  await tick();
  openAgentDrawer("b");
  await drain({ events: [], hasMore: false });
  await more;
  assert.equal(state.inspector.kind, "agent");
  assert.equal(decide(), "refresh", "the agent drawer inherited loadingMore");
});

test("CONTROL: a drawer whose own fetch is in flight is still skipped", async () => {
  // The guard this fix must leave working: a History drawer mid-fetch is not refreshed over itself.
  state.inspector = emptyInspector();
  const history = openCompactionHistory("x");
  await tick();
  assert.equal(decide(), "loading");
  await drain({ spawnRequests: [] });
  await history;
  assert.equal(decide(), "refresh");
});

test("reopening the same kind of drawer keeps its state; another kind starts clean", () => {
  state.inspector = { ...emptyInspector(), kind: "history", agentId: "x", loaded: true, loading: true };
  assert.equal(inspectorOpening("history", { agentId: "x" }).loaded, true, "a refresh of the same drawer lost what it shows");
  const other = inspectorOpening("agent", { agentId: "b" });
  assert.equal(other.loading, undefined);
  assert.equal(other.loaded, undefined);
  assert.deepEqual({ ...other, kind: "", agentId: undefined }, { ...emptyInspector(), agentId: undefined });
});

test("no drawer opener copies the previous drawer's state under a new kind", () => {
  // DERIVED, so a drawer added later is covered: any `state.inspector = { ...state.inspector, kind: ... }`
  // in a module is the defect's shape. Openers use `inspectorOpening` (state.mjs).
  const shape = /state\.inspector\s*=\s*\{\s*\.\.\.state\.inspector\s*,\s*kind\s*:/;
  const dir = new URL("./", import.meta.url);
  const offenders = readdirSync(dir)
    .filter((f) => (f.endsWith(".mjs") || f === "app.js") && !f.endsWith(".test.mjs"))
    .filter((f) => shape.test(readFileSync(new URL(f, dir), "utf8")));
  assert.deepEqual(offenders, []);
  assert.ok(shape.test("state.inspector = { ...state.inspector, kind: 'x' };"), "CONTROL: the pattern finds the defect's shape");
});
