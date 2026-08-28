// A partial contracts scan tells the operator it is partial.
//
// The endpoint scans a bounded superset before filtering, because a contract's state is derived in
// Python and the SQL standing in for it is deliberately wider. When that scan hits its ceiling the
// list is a page, not the whole answer, and the response says so with `truncated`.
//
// A FLAG NOTHING READS IS NOT A FIX. `truncated` was added to the API in the same change that fixed
// the count, and for one commit nothing consumed it -- the same "field with no reader" shape this
// review has been finding elsewhere. Under a capped scan "No contracts match" and "none exist" are
// different facts that render identically, which is the whole reason the flag exists.

import assert from "node:assert/strict";
import { test } from "node:test";

import { state } from "./state.mjs";
import { loadContractsForState, renderContracts } from "./work-loop-actions.mjs";

/** The smallest DOM the renderer touches. */
function harness({ contracts = [], truncated = false, view = "list" } = {}) {
  const nodes = new Map();
  const make = (id) => ({
    id,
    value: id === "contract-state" ? "all" : "",
    innerHTML: "",
    classList: { toggle() {} },
  });
  for (const id of ["contract-state", "contract-category", "contract-list", "diagnostics-bulk"]) {
    nodes.set(id, make(id));
  }
  const element = () => ({
    className: '', textContent: '', innerHTML: '', style: {},
    dataset: {}, classList: { add() {}, remove() {}, toggle() {} },
    appendChild() {}, remove() {}, setAttribute() {}, addEventListener() {},
  });
  globalThis.document = {
    getElementById: (id) => nodes.get(id) || null,
    querySelectorAll: () => [],
    querySelector: () => null,
    // `toast` builds a node on the failure path, and the loader reaches it whenever the stubbed
    // fetch is not what it expected -- so a missing createElement hides the real assertion
    // behind a TypeError about the harness.
    createElement: element,
    body: { appendChild() {}, removeChild() {} },
  };
  state.contracts = contracts;
  state.contractsTruncated = truncated;
  state.contractView = view;
  return nodes;
}

const CONTRACT = {
  id: "run-1", subject: "s", preview: "p", from: "a", targetAgentId: "b",
  state: "missing_reply", status: "completed", overdue: false, category: "direct",
  replyState: "awaiting", requestedAt: "2026-08-01T00:00:00Z",
};

test("a complete scan says nothing about being partial", () => {
  // The control. A banner that always renders carries no information, and every assertion below
  // would pass on a renderer that hardcoded it.
  const nodes = harness({ contracts: [CONTRACT], truncated: false });
  renderContracts();
  assert.doesNotMatch(nodes.get("contract-list").innerHTML, /partial scan/i);
});

test("a capped scan says so above the list", () => {
  const nodes = harness({ contracts: [CONTRACT], truncated: true });
  renderContracts();
  assert.match(nodes.get("contract-list").innerHTML, /partial scan/i);
});

test("a capped scan that matched NOTHING still says so", () => {
  // The case that matters most, and the one an empty-state alone gets wrong: zero rows under a
  // capped scan is "none in the part I looked at", not "none exist".
  const nodes = harness({ contracts: [], truncated: true });
  renderContracts();
  const html = nodes.get("contract-list").innerHTML;
  assert.match(html, /partial scan/i, "an empty capped result claimed there was nothing to find");
  assert.match(html, /No contracts match/, "the empty state itself stopped rendering");
});

test("the board view carries the notice too", () => {
  // Two render branches, and a notice on only one of them is a notice that disappears when the
  // operator switches view.
  const nodes = harness({ contracts: [CONTRACT], truncated: true, view: "board" });
  renderContracts();
  assert.match(nodes.get("contract-list").innerHTML, /partial scan/i);
});

// ---- the LOADER has to store it, not just the renderer react to it ------------------------------
//
// The cases above set `state.contractsTruncated` directly, so they prove the renderer responds to
// the flag and nothing more. Deleting the line that STORES it left all four green -- the same
// "helper proven, call site unproven" gap this review keeps finding, this time in my own test. These
// drive the real loader over a stubbed `fetch`.

test("the loader stores the flag the endpoint sent", async () => {
  harness();
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    // `api()` reads `response.text()` and parses it, so a stub offering `json()` is not a stub of
    // this client -- it fails inside the helper with a message about `length`, nowhere near the
    // assertion. Read the caller before stubbing for it.
    return { ok: true, status: 200, text: async () => JSON.stringify({ contracts: [CONTRACT], truncated: true }) };
  };
  try {
    await loadContractsForState("missing_reply", false);
  } finally {
    delete globalThis.fetch;
  }
  assert.equal(calls.length, 1, "the loader did not call the endpoint at all");
  assert.match(calls[0], /state=missing_reply/, "the loader asked for the wrong thing");
  assert.equal(state.contractsTruncated, true, "the endpoint said truncated and the loader dropped it");
  assert.equal(state.contracts.length, 1);
});

test("the loader clears the flag when the endpoint does not set it", async () => {
  // Anti-vacuity, and a real failure mode: a flag that is only ever set to true stays true for the
  // rest of the session, and the notice becomes permanent furniture the operator learns to ignore.
  harness({ truncated: true });
  globalThis.fetch = async () => ({
    ok: true, status: 200, text: async () => JSON.stringify({ contracts: [], truncated: false }),
  });
  try {
    await loadContractsForState("closed", false);
  } finally {
    delete globalThis.fetch;
  }
  assert.equal(state.contractsTruncated, false, "a complete scan left the previous warning standing");
});
