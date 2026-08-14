// Real tests for the overview summary tiles.
//
// `selectedDiagnostics` is the piece with actual logic, and both of its rules are the kind that fail
// quietly: a diagnostic key is `kind:id`, so an id that itself contains a colon has to be rejoined rather
// than truncated, and a selection whose record has since disappeared must be dropped rather than acted on
// in bulk. Neither was reachable by a test while this lived in app.js.
//
// SEALING. `state` is a shared singleton, so every field these read is rebuilt per test; `document` does
// not exist in Node and is installed only while rendering.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import {
  metric,
  renderDiagnosticsSummary,
  renderMetrics,
  renderUsageConsumption,
  selectedDiagnostics,
} from "./summary-tiles.mjs";

function seed({ agents = [], contracts = [], runs = [], selected = [] } = {}) {
  state.agents = agents;
  state.contracts = contracts;
  state.runs = runs;
  state.selectedDiagnosticIds = new Set(selected);
}

function renderInto(id, fn) {
  const host = { innerHTML: "" };
  const had = "document" in globalThis;
  globalThis.document = { getElementById: (want) => (want === id ? host : null) };
  try {
    fn();
    return host.innerHTML;
  } finally {
    if (!had) delete globalThis.document;
  }
}

test("a diagnostic id containing a colon survives the kind:id split", () => {
  // `const [kind, ...rest] = key.split(':')` then `rest.join(':')`. Truncating at the first colon would
  // silently select nothing for every id with one in it — and run ids in this system do carry colons.
  seed({ runs: [{ id: "run:2026:abc" }], selected: ["run:run:2026:abc"] });
  const got = selectedDiagnostics();
  assert.equal(got.length, 1, "the id must be rejoined, not truncated at the first colon");
  assert.equal(got[0].id, "run:2026:abc");
  assert.equal(got[0].kind, "run");
});

test("a selection whose record has vanished is dropped", () => {
  // The selection outlives the poll that refreshes the lists. Keeping a stale key would let a bulk action
  // fire against a record that is no longer there.
  seed({ contracts: [{ id: "c1" }], selected: ["contract:c1", "contract:gone", "run:also-gone"] });
  assert.deepEqual(selectedDiagnostics().map((s) => s.id), ["c1"]);
});

test("both kinds are resolved, and an unknown kind is ignored", () => {
  seed({
    contracts: [{ id: "c1", subject: "a contract" }],
    runs: [{ id: "r1", subject: "a run" }],
    selected: ["contract:c1", "run:r1", "session:s1", "c1"],
  });
  const got = selectedDiagnostics();
  assert.deepEqual(got.map((s) => s.kind), ["contract", "run"]);
  assert.equal(got[0].item.subject, "a contract", "the resolved record travels with the selection");
});

test("ids are compared as strings, so a numeric id still resolves", () => {
  seed({ runs: [{ id: 42 }], selected: ["run:42"] });
  assert.equal(selectedDiagnostics().length, 1);
});

test("metric renders a labelled tile and carries its tone", () => {
  const tile = metric("Working now", 3, "working");
  assert.ok(tile.includes("Working now"));
  assert.ok(tile.includes("3"));
  assert.ok(tile.includes("working"), "the tone drives the colour — a tile without it reads as neutral");
});

test("metric escapes its label", () => {
  // The tile itself contains a literal `<b>` around the VALUE, so "no <b> anywhere" was the wrong
  // assertion — my test, not the code. What matters is that the label's markup arrives escaped.
  const tile = metric("<b>x</b>", 1, "ok");
  assert.ok(tile.includes("&lt;b&gt;x&lt;/b&gt;"), "a label carrying markup must be escaped");
  assert.ok(!tile.includes("<b>x</b>"), "…and must not be rendered as markup");
});

test("the metrics tiles count agents by RESOLVED status, not by raw string", () => {
  seed({
    agents: [
      { id: "a", status: "working" },
      { id: "b", status: "blocked" },
      { id: "c", status: "online" },
      { id: "d", status: "offline" },
    ],
    contracts: [{ id: "c1", overdue: true }, { id: "c2", state: "queued" }],
  });
  const html = renderInto("metrics", renderMetrics);
  assert.ok(html.includes("Active agents"));
  assert.ok(html.includes("Working now"));
  assert.ok(html.includes("Blocked agents"));
  assert.ok(html.includes("Overdue work"));
  assert.ok(html.includes("Queued contracts"));
  // The tile renders VALUE before LABEL (`<b>3</b><span>Active agents</span>`), which my first version of
  // this assertion had backwards. offline is excluded from "active"; the other three count.
  assert.ok(html.includes("<b>3</b><span>Active agents</span>"),
    "active counts online+working+blocked, not offline");
});

test("a zero count renders neutral rather than alarming", () => {
  // A permanently red "Blocked agents: 0" trains the operator to ignore the tile that matters.
  seed({ agents: [{ id: "a", status: "online" }], contracts: [] });
  const html = renderInto("metrics", renderMetrics);
  assert.ok(html.includes("neutral"), "zero-valued tiles must be neutral-toned");
});

test("the diagnostics summary and usage tiles render without a selection", () => {
  seed({});
  const diag = renderInto("diagnostics-summary", renderDiagnosticsSummary);
  assert.equal(typeof diag, "string");
  const usage = renderInto("usage-consumption", renderUsageConsumption);
  assert.equal(typeof usage, "string");
});

test("the diagnostics and usage tiles are no-ops when their host is absent", () => {
  // They run on the poll from every page, not only the one they belong to, so both guard explicitly.
  seed({});
  const had = "document" in globalThis;
  globalThis.document = { getElementById: () => null };
  try {
    renderDiagnosticsSummary();
    renderUsageConsumption();
  } finally {
    if (!had) delete globalThis.document;
  }
});

test("KNOWN GAP: renderMetrics THROWS when its host is absent, unlike its two siblings", () => {
  // Found by writing this test. `renderDiagnosticsSummary` and `renderUsageConsumption` both open with
  // `const host = byId(...); if (!host) return;`. `renderMetrics` assigns `byId('metrics').innerHTML`
  // directly, so a missing host is a TypeError rather than a no-op.
  //
  // NOT LIVE TODAY: `#metrics` is present in index.html, and all three run on the same poll. It is a
  // latent inconsistency, not a current failure — but the two siblings guard for exactly this reason.
  //
  // NOT FIXED HERE, deliberately: this slice moves the declaration BYTE-IDENTICALLY and the reconstruction
  // proof compares it against the pristine fixture, so adding the guard would fail the proof. Pinned as
  // current behaviour; whoever adds the guard will see this fail and delete it on purpose.
  seed({});
  const had = "document" in globalThis;
  globalThis.document = { getElementById: () => null };
  try {
    assert.throws(() => renderMetrics(), /innerHTML|null/,
      "if this no longer throws, the guard was added — delete this test and the KNOWN GAP note");
  } finally {
    if (!had) delete globalThis.document;
  }
});
