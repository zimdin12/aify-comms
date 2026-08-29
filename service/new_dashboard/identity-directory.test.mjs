// Real tests for the identity directory.
//
// The counts this panel shows — how many agents are managed, how many resident, and the fleet's total
// unread — are summary arithmetic over live state that nothing has ever checked. While this lived in
// app.js it was reachable only by source regex, which can prove the template was written and nothing about
// whether the numbers in it are right. A directory that miscounts is worse than one that is missing: it is
// consulted precisely when the operator is auditing what is registered.
//
// SEALING. `document` does not exist in Node; a minimal fake is installed per test and removed afterwards.
// `state` is a shared singleton, so every field read here is rebuilt per test.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import { openIdentityDirectory } from "./identity-directory.mjs";

function fakeEl(classes = []) {
  const set = new Set(classes);
  return {
    innerHTML: "",
    classList: { add: (c) => set.add(c), remove: (c) => set.delete(c), contains: (c) => set.has(c) },
  };
}

function render({ agents = [], sessions = [], environments = [] } = {}) {
  state.agents = agents;
  state.sessions = sessions;
  state.environments = environments;
  state.inspector = {};
  const els = { inspector: fakeEl(), "inspector-content": fakeEl() };
  const had = "document" in globalThis;
  globalThis.document = { getElementById: (id) => els[id] || null };
  try {
    openIdentityDirectory();
    return { html: els["inspector-content"].innerHTML, els };
  } finally {
    if (!had) delete globalThis.document;
  }
}

const stat = (html, label) =>
  Number((new RegExp(`<dt>${label}</dt><dd>(\\d+)</dd>`).exec(html) || [])[1]);

test("managed and resident are counted from the agent's mode, and add up to the total", () => {
  const { html } = render({
    agents: [
      { id: "a", sessionMode: "managed" },
      { id: "b", sessionMode: "MANAGED" },        // case must not matter
      { id: "c", sessionMode: "resident" },
      { id: "d" },                                 // absent mode defaults to resident
    ],
  });
  assert.equal(stat(html, "Managed"), 2);
  assert.equal(stat(html, "Resident / manual"), 2,
    "resident is the REMAINDER — an agent counted in neither column would vanish from the audit");
});

test("the mode falls back to the SESSION when the agent row omits it", () => {
  // IT SEEDED TWO SPELLINGS on the claim that "the server has shipped `mode` and `session_mode` on
  // different rows". `_agent_session_to_dict` emits `mode`, and has no branch that emits
  // `session_mode` -- so the second row was testing a shape the service cannot produce, and the
  // reader kept a dead alternate to satisfy it.
  const { html } = render({
    agents: [{ id: "a" }, { id: "b" }],
    sessions: [
      { id: "s1", agentId: "a", mode: "managed" },
      { id: "s2", agentId: "b", mode: "managed" },
    ],
  });
  assert.equal(stat(html, "Managed"), 2, "an agent row without a mode must not read as resident by default");
});

test("total unread sums both field spellings, and absent counts contribute 0", () => {
  const { html } = render({
    agents: [
      { id: "a", unread: 3 },
      { id: "b", unreadCount: 4 },
      { id: "c" },
      { id: "d", unread: null },
      { id: "e", unread: "5" },     // the server has shipped this as a string
    ],
  });
  assert.equal(stat(html, "Total unread"), 12);
});

test("KNOWN GAP: a truthy non-numeric unread renders the total as NaN", () => {
  // Found by writing this test, not by reading the code. `Number(a.unread || a.unreadCount || 0)` treats a
  // truthy junk value as a number, so one malformed row turns the whole fleet total into "NaN" — the
  // `|| 0` fallback only catches falsy values, which is not the same thing.
  //
  // NOT FIXED HERE, deliberately: this slice moves the declaration BYTE-IDENTICALLY and the reconstruction
  // proof compares it against the pristine fixture, so editing the body would fail the proof. Pinned as the
  // current behaviour so the gap is visible rather than absent; whoever repairs it will see this test fail
  // and update it on purpose.
  const { html } = render({ agents: [{ id: "a", unread: 3 }, { id: "b", unread: "nope" }] });
  assert.match(html, /<dt>Total unread<\/dt><dd>NaN<\/dd>/,
    "if this now reads 3, the guard was fixed — update this test and delete the KNOWN GAP note");
});

test("rows are sorted by id so the directory is stable between polls", () => {
  const { html } = render({ agents: [{ id: "zeta" }, { id: "alpha" }, { id: "mid" }] });
  const order = [...html.matchAll(/<strong>([a-z]+)<\/strong>/g)].map((m) => m[1]);
  assert.deepEqual(order, ["alpha", "mid", "zeta"],
    "an unsorted directory reshuffles under the operator on every refresh");
});

test("an environment label is resolved, and a session with no binding reads as a dash", () => {
  // THE CASE CHANGED WITH THE READER, and the old one is worth recording. It seeded
  // `environment_id: "unassigned"` -- a STORED value -- and required a dash, because the column and
  // the field reader's sentinel were the same string and this table could not tell them apart. It
  // was matching on the sentinel's spelling, so it would have passed on a real environment named
  // `unassigned` and failed on a session that simply had none. The reader answers '' now, so the
  // absence is what this asserts.
  const { html } = render({
    agents: [{ id: "a" }, { id: "b" }],
    sessions: [
      { id: "s1", agentId: "a", environmentId: "env-1" },
      { id: "s2", agentId: "b" },
    ],
    environments: [{ id: "env-1", label: "Windows on host" }],
  });
  assert.ok(html.includes("Windows on host"), "a known environment shows its label, not its id");
  assert.ok(!html.includes("unassigned"), "a session with no binding must render a dash, not a word");
});

test("an empty fleet says so instead of rendering an empty table", () => {
  const { html } = render({ agents: [] });
  assert.ok(html.includes("No identities"), "an empty table body reads as a broken panel");
  assert.equal(stat(html, "Total unread"), 0);
});

test("opening the directory marks the inspector and clears run styling", () => {
  const { els } = render({ agents: [{ id: "a" }] });
  assert.equal(state.inspector.kind, "identity-directory");
  assert.equal(state.inspector.runId, "", "a leftover run id must not survive into this panel");
  assert.equal(els.inspector.classList.contains("open"), true);
  assert.equal(els.inspector.classList.contains("run-inspector-sheet"), false);
});

test("every agent gets Details and Remove actions keyed on its id", () => {
  const { html } = render({ agents: [{ id: "coder" }] });
  assert.ok(html.includes('data-agent-details="coder"'));
  assert.ok(html.includes('data-agent-remove="coder"'));
});
