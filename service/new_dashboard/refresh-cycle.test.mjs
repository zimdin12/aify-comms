// The poll cycle, tested by CALLING it against a stubbed `fetch`.
//
// This is the function the whole dashboard is rebuilt from every ~15 seconds, and until it left app.js
// nothing could import it — so its central property, PARTIAL FAILURE TOLERANCE, was never asserted
// anywhere. The property is not decorative: with `Promise.all` a single dropped request under poll load
// rejected the whole refresh, `renderAll` never ran, and every agent's status froze on its last render
// while the page looked healthy. `Promise.allSettled` is the fix, and the tests below reintroduce the
// failure it guards rather than checking that the string "allSettled" appears in the source.

import assert from "node:assert/strict";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { state } from "./state.mjs";
import { runRefreshCycle } from "./refresh-cycle.mjs";
import { resetRefreshHistory } from "./refresh-status.mjs";

/** The ten paths the cycle fetches, in the order they are requested. */
const OK_BODIES = {
  "/agents": { agents: [{ id: "a1", name: "one" }] },
  "/contracts": { contracts: [{ id: "c1" }] },
  "/messages/inbox": { messages: [{ id: "m-inbox" }] },
  "/messages/recent": { messages: [{ id: "m-recent" }] },
  "/dispatch/runs": { runs: [{ id: "r1" }] },
  "/sessions": { sessions: [{ id: "s1", agentId: "a1", terminalId: "t1" }] },
  "/environments": { environments: [{ id: "e1" }] },
  "/spawn-requests": { spawnRequests: [{ id: "sr1" }] },
  "/stats": { total: 7 },
  "/settings": { dashboard_refresh_seconds: 15 },
};

function bodyFor(path) {
  for (const [prefix, body] of Object.entries(OK_BODIES)) if (path.startsWith(prefix)) return body;
  return {};
}

/** Elements the cycle writes the status chip into, recorded so the chip can be asserted. */
function makeElements() {
  const els = new Map();
  for (const id of ["api-status", "contract-state"]) {
    els.set(id, { id, textContent: "", className: "", value: "", style: {}, classList: { add() {}, remove() {}, toggle() {} } });
  }
  return els;
}

/**
 * Run one cycle with `reject` naming the path prefixes whose fetch should fail.
 * Returns the recorded calls plus the status-chip element.
 */
async function cycle({ reject = [], extraDeps = {}, filesPage = null } = {}) {
  const els = makeElements();
  const saved = {
    document: globalThis.document,
    fetch: globalThis.fetch,
    setTimeout: globalThis.setTimeout,
    state: { ...state },
  };
  const requested = [];
  const calls = { renderAll: 0, evaluateFlowGates: 0, refreshOpenInspector: 0, armRefreshTimer: 0, loadContractsForState: 0 };

  globalThis.document = {
    getElementById: (id) => {
      // The Files page exists only when a test asks for it. Absent is the honest default and means
      // "cannot tell", which shouldLoadFiles answers by fetching -- so every other test in this file
      // keeps its ten slices.
      if (id === "page-files" && filesPage) {
        return { classList: { contains: (c) => c === "active" && filesPage === "open" } };
      }
      return els.get(id) || null;
    },
    querySelector: () => null,
    querySelectorAll: () => [],
    documentElement: { style: { setProperty() {} }, dataset: {}, setAttribute() {}, classList: { add() {}, remove() {}, toggle() {} } },
    body: { style: { setProperty() {} }, classList: { add() {}, remove() {}, toggle() {} }, dataset: {} },
    title: "",
  };
  globalThis.fetch = async (url) => {
    const path = String(url);
    requested.push(path);
    if (reject.some((p) => path.includes(p))) throw new TypeError("Failed to fetch");
    return { ok: true, status: 200, statusText: "OK", text: async () => JSON.stringify(bodyFor(path.replace(/^https?:\/\/[^/]*/, ""))) };
  };

  setApiBase("");
  Object.assign(state, {
    agents: [], contracts: [], messages: [], runs: [], sessions: [], environments: [],
    spawnRequests: [], stats: {}, settings: {}, loaded: false, terminalOwners: new Map(),
  });

  try {
    await runRefreshCycle({
      armRefreshTimer: () => { calls.armRefreshTimer += 1; },
      chatController: { close() {}, render() {}, renderRail() {}, renderConversation() {} },
      evaluateFlowGates: () => { calls.evaluateFlowGates += 1; },
      loadContractsForState: async () => { calls.loadContractsForState += 1; },
      refreshOpenInspector: () => { calls.refreshOpenInspector += 1; },
      renderAll: () => { calls.renderAll += 1; },
      ...extraDeps,
    });
  } finally {
    globalThis.document = saved.document;
    globalThis.fetch = saved.fetch;
  }
  return { calls, requested, chip: els.get("api-status") };
}

test("a clean cycle fetches all ten slices and renders", async () => {
  const { calls, requested, chip } = await cycle();
  // TWELVE, not ten: the ten parallel slices plus the two sequential follow-ups the cycle also owns
  // (chat channels and shared files), each wrapped in its own try/catch so it keeps prior data.
  assert.equal(requested.length, 12, `expected twelve fetches, got ${requested.length}`);
  for (const path of ["/agents", "/sessions", "/settings", "/shared"]) {
    assert.ok(requested.some((r) => r.includes(path)), `${path} must be fetched each cycle`);
  }
  assert.equal(calls.renderAll, 1);
  assert.equal(calls.evaluateFlowGates, 1);
  assert.equal(calls.refreshOpenInspector, 1);
  assert.equal(chip.textContent, "live");
  assert.deepEqual(state.agents, [{ id: "a1", name: "one" }]);
  assert.equal(state.loaded, true);
});

test("ONE REJECTED SLICE DOES NOT STOP THE CYCLE — the Promise.all defect, reintroduced", async () => {
  // With `Promise.all` this single rejection propagated: no state applied, `renderAll` never called,
  // the page frozen on its last render. Every other slice must still land.
  const { calls, chip } = await cycle({ reject: ["/contracts"] });
  assert.equal(calls.renderAll, 1, "renderAll must still run when one slice fails");
  assert.deepEqual(state.agents, [{ id: "a1", name: "one" }], "the agent roster must still be applied");
  assert.deepEqual(state.runs, [{ id: "r1" }]);
  assert.equal(state.loaded, true);
  // And the chip stays green: one non-critical blip is not worth alarming the operator over.
  assert.equal(chip.textContent, "live");
});

test("a failed slice KEEPS ITS LAST-GOOD VALUE rather than being blanked", async () => {
  // The other half of resilience. Applying `undefined` on rejection would empty the panel, which
  // reads to an operator exactly like "the server says there is nothing here".
  await cycle();
  const previousRuns = state.runs;
  assert.deepEqual(previousRuns, [{ id: "r1" }]);
  // Second cycle with /runs down — but cycle() resets state, so assert against a fresh seed instead.
  const els = makeElements();
  const savedDoc = globalThis.document;
  const savedFetch = globalThis.fetch;
  globalThis.document = { getElementById: (id) => els.get(id) || null, querySelector: () => null, querySelectorAll: () => [], documentElement: { style: { setProperty() {} }, dataset: {}, setAttribute() {}, classList: { add() {}, remove() {}, toggle() {} } }, body: { style: { setProperty() {} }, classList: { add() {}, remove() {}, toggle() {} }, dataset: {} }, title: "" };
  globalThis.fetch = async (url) => {
    const path = String(url);
    if (path.includes("/dispatch/runs")) throw new TypeError("Failed to fetch");
    return { ok: true, status: 200, statusText: "OK", text: async () => JSON.stringify(bodyFor(path)) };
  };
  state.runs = [{ id: "PRIOR" }];
  try {
    await runRefreshCycle({
      armRefreshTimer: () => {}, chatController: { close() {}, render() {}, renderRail() {}, renderConversation() {} },
      evaluateFlowGates: () => {}, loadContractsForState: async () => {}, refreshOpenInspector: () => {}, renderAll: () => {},
    });
  } finally {
    globalThis.document = savedDoc;
    globalThis.fetch = savedFetch;
  }
  assert.deepEqual(state.runs, [{ id: "PRIOR" }], "a rejected slice must not blank what it last held");
});

test("EVERY slice failing still renders, and says reconnecting rather than lying", async () => {
  // The server-fully-down case. Rendering is still required — the page keeps its last-good content —
  // but the chip must NOT read live, and `loaded` must not flip, or the rail shows a confident
  // "No agents." while nothing was actually fetched.
  const { calls, chip } = await cycle({ reject: ["/"] });
  assert.equal(calls.renderAll, 1, "a total outage must still re-render, not throw");
  assert.equal(chip.textContent, "reconnecting");
  assert.equal(chip.className, "status-chip warn");
  assert.equal(state.loaded, false, "loaded must stay false until the roster actually arrives");
});

test("a single blip of a non-core slice does not move the chip", async () => {
  // The property the original test was protecting, and it still holds: "agents are the core slice.
  // Stats/settings blipping is noise." A chip that flickers amber on every transient miss is a chip
  // the operator learns to ignore, which costs more than it saves.
  //
  // What changed is the RULE behind it. It used to be "green whenever /agents succeeded, whatever
  // else failed", which also painted nine-of-ten-failed green. It is now persistence: a slice must
  // miss on two consecutive cycles to count as stale. This test states the blip case explicitly
  // instead of relying on whatever the previous test left behind.
  resetRefreshHistory();
  const { chip } = await cycle({ reject: ["/stats", "/settings"] });
  assert.equal(chip.textContent, "live");
  assert.equal(chip.className, "status-chip ok");
  assert.match(chip.title, /Retrying/, "a blip should still be visible in the tooltip");
});

test("a slice that misses twice running is reported stale, and named", async () => {
  // The half that did not exist. Nine of ten slices could fail and the chip said live, while the
  // resilient poll showed each one's last-good value -- so stale data rendered identically to data
  // that simply had not changed. There was no other tell anywhere on the page.
  resetRefreshHistory();
  await cycle({ reject: ["/stats", "/settings"] });          // first miss: still a blip
  const { chip } = await cycle({ reject: ["/stats", "/settings"] });  // second: now stale
  assert.equal(chip.textContent, "2 stale");
  assert.equal(chip.className, "status-chip warn");
  assert.match(chip.title, /stats/);
  assert.match(chip.title, /settings/);
});

test("a slice that recovers stops being counted", async () => {
  resetRefreshHistory();
  await cycle({ reject: ["/stats"] });
  await cycle({ reject: ["/stats"] });
  const { chip } = await cycle();
  assert.equal(chip.textContent, "live", "a recovered slice stayed on the stale list");
});

test("the roster failing alone turns the chip amber even though nine slices landed", async () => {
  const { chip } = await cycle({ reject: ["/agents"] });
  assert.equal(chip.textContent, "reconnecting");
  assert.equal(state.loaded, false);
});

test("session rows seed terminalOwners so a console can be traced back to its agent", async () => {
  await cycle();
  assert.equal(state.terminalOwners.get("t1"), "a1");
});

test("armRefreshTimer runs only when /settings actually returned", async () => {
  // It honours dashboard_refresh_seconds. Arming off a rejected fetch would re-arm from stale settings.
  const clean = await cycle();
  assert.equal(clean.calls.armRefreshTimer, 1);
  const degraded = await cycle({ reject: ["/settings"] });
  assert.equal(degraded.calls.armRefreshTimer, 0, "no settings, no re-arm");
});

test("a non-default Work-loop State filter is re-applied after the open-scope base fetch", async () => {
  // The base fetch is open-scope, so a terminal selection (Answered/Failed/…) emptied ~15s after the
  // operator chose it, when the poll overwrote state.contracts.
  const withFilter = {
    getElementById: (id) => (id === "contract-state" ? { value: "failed" } : { textContent: "", className: "" }),
  };
  const savedDoc = globalThis.document;
  const savedFetch = globalThis.fetch;
  globalThis.document = { ...withFilter, querySelector: () => null, querySelectorAll: () => [], documentElement: { style: { setProperty() {} }, dataset: {}, setAttribute() {}, classList: { add() {}, remove() {}, toggle() {} } }, body: { style: { setProperty() {} }, classList: { add() {}, remove() {}, toggle() {} }, dataset: {} }, title: "" };
  globalThis.fetch = async (url) => ({ ok: true, status: 200, statusText: "OK", text: async () => JSON.stringify(bodyFor(String(url))) });
  let reloaded = 0;
  try {
    await runRefreshCycle({
      armRefreshTimer: () => {}, chatController: { close() {}, render() {}, renderRail() {}, renderConversation() {} },
      evaluateFlowGates: () => {}, loadContractsForState: async () => { reloaded += 1; }, refreshOpenInspector: () => {}, renderAll: () => {},
    });
  } finally {
    globalThis.document = savedDoc;
    globalThis.fetch = savedFetch;
  }
  assert.equal(reloaded, 1, "a non-open State selection must be re-fetched after the base poll");
});

test("the open conversation closes when its agent disappears from the roster", async () => {
  // Stale-selection guard: without it the header, timeline and composer stay live against a deleted
  // agent, and a send goes nowhere the operator can see.
  let closed = 0;
  state.chat = { ...(state.chat || {}), selected: "dm:GONE", channels: [] };
  await cycle({ extraDeps: { chatController: { close() { closed += 1; }, render() {}, renderRail() {}, renderConversation() {} } } });
  assert.equal(closed, 1, "a dm: selection naming no live agent must close");
});

test("the six injected names are NOT imported — that is what keeps app.js out of this module", async () => {
  const fs = await import("node:fs");
  const src = fs.readFileSync(new URL("./refresh-cycle.mjs", import.meta.url), "utf8");
  for (const name of ["armRefreshTimer", "chatController", "evaluateFlowGates",
    "loadContractsForState", "refreshOpenInspector", "renderAll"]) {
    assert.doesNotMatch(src, new RegExp(`^import .*\\b${name}\\b`, "m"), `${name} must be injected`);
  }
});

test("the closed Files page is not polled", async () => {
  // THE CALL SITE, not the predicate. shouldLoadFiles has its own tests and every one of them would
  // still pass with this guard deleted -- what has to hold is that the cycle actually asks it. /shared
  // is 113,854 bytes for 388 files against the live service, so polling it unseen costs 8.0 MB an hour
  // per tab at the default 15s refresh.
  const { requested } = await cycle({ filesPage: "closed" });
  assert.equal(requested.filter((u) => u.includes("/shared")).length, 0, "fetched a list nobody can see");
});

test("the open Files page is still polled", async () => {
  // The other half, and the one that fails if the guard is inverted -- a skip that never un-skips looks
  // exactly like a working optimisation until somebody opens the page.
  const { requested } = await cycle({ filesPage: "open" });
  assert.equal(requested.filter((u) => u.includes("/shared")).length, 1, "the visible page went unfetched");
});
