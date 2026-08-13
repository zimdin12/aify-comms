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
  diagnosticKey,
  filtered,
  renderContractBoard,
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
