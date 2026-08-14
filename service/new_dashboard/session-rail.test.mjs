// Real tests for the Sessions rail: they CALL the functions, they do not grep for them.
//
// app.js is reachable only by source-regex tests, which can prove a line was written and nothing about
// whether it works. Everything moved into `session-rail.mjs` becomes testable for the first time, and
// `groupedSessionsByEnvironment` in particular carries four independent behaviours — status filter, global
// find, environment grouping, superseded collapse — that nothing has ever exercised.
//
// SEALING. `state` is a shared singleton by design, so a test that mutates it and walks away poisons the
// next one. Every test here rebuilds the fields it uses through `seed()`. `sessionGroupCollapsed` reads
// `localStorage`, which does not exist in Node: it is installed per test and removed afterwards, so the
// suite cannot pass by accident on a host that happens to provide one.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import {
  SESSION_FILTER_KINDS,
  agentForSession,
  groupedSessionsByEnvironment,
  selectedSessionIds,
  sessionGroupCollapsed,
} from "./session-rail.mjs";

function seed({ sessions = [], agents = [], environments = [], filter = "", statusFilter = null,
                selected = [], showSuperseded = true } = {}) {
  state.sessions = sessions;
  state.agents = agents;
  state.environments = environments;
  state.filter = filter;
  state.sessionStatusFilter = statusFilter;
  state.selectedSessionIds = new Set(selected);
  state.showSupersededSessions = showSuperseded;
}

const session = (id, agentId, environmentId, extra = {}) =>
  ({ id, agent_id: agentId, environment_id: environmentId, ...extra });

test("agentForSession resolves through the agents list, and never returns undefined", () => {
  seed({ sessions: [], agents: [{ id: "coder", status: "online" }] });
  assert.equal(agentForSession(session("s1", "coder", "env")).status, "online");

  // An EMPTY OBJECT, not undefined, is the contract: callers read `.status` off the result directly, so
  // returning undefined would throw at the render rather than show an unknown agent.
  assert.deepEqual(agentForSession(session("s1", "ghost", "env")), {});
});

test("groupedSessionsByEnvironment groups by environment and sorts by LABEL, not by id", () => {
  seed({
    sessions: [session("s1", "a", "zeta"), session("s2", "b", "alpha"), session("s3", "c", "zeta")],
    environments: [{ id: "zeta", label: "Aardvark" }, { id: "alpha", label: "Zebra" }],
  });
  const groups = groupedSessionsByEnvironment();
  assert.deepEqual(groups.map((g) => g.label), ["Aardvark", "Zebra"],
    "sorting is by the human-readable label; sorting by id would give the opposite order here");
  assert.deepEqual(groups.map((g) => g.sessions.length), [2, 1]);
});

test("an environment with no record falls back to its id as the label", () => {
  seed({ sessions: [session("s1", "a", "orphan-env")], environments: [] });
  const [group] = groupedSessionsByEnvironment();
  assert.equal(group.label, "orphan-env", "a session must still be listed when its environment is unknown");
});

test("the global find narrows by id, agent, workspace and runtime", () => {
  const rows = [
    session("alpha-1", "coder", "env", { workspace: "/srv/api" }),
    session("beta-2", "tester", "env", { workspace: "/srv/web" }),
  ];
  for (const [needle, expected] of [["alpha", ["alpha-1"]], ["tester", ["beta-2"]],
                                    ["/srv/web", ["beta-2"]], ["srv", ["alpha-1", "beta-2"]]]) {
    seed({ sessions: rows, environments: [], filter: needle });
    const found = groupedSessionsByEnvironment().flatMap((g) => g.sessions.map((s) => s.id));
    assert.deepEqual(found.sort(), expected, `find "${needle}"`);
  }
});

test("the find is case-insensitive and ignores surrounding whitespace", () => {
  seed({ sessions: [session("Alpha-1", "coder", "env")], environments: [], filter: "  ALPHA  " });
  assert.equal(groupedSessionsByEnvironment().length, 1);
});

test("an EMPTY status filter means all, not none", () => {
  // The distinction the code makes with `filter && filter.size`. Treating an empty Set as "match nothing"
  // would silently blank the whole rail the moment the operator cleared the filter.
  const rows = [session("s1", "a", "env")];
  seed({ sessions: rows, environments: [], statusFilter: new Set() });
  assert.equal(groupedSessionsByEnvironment().length, 1, "an empty filter must not hide everything");

  seed({ sessions: rows, environments: [], statusFilter: null });
  assert.equal(groupedSessionsByEnvironment().length, 1, "…and neither must an absent one");
});

test("selectedSessionIds drops ids whose session is gone", () => {
  // The rail keeps a selection across refreshes; a stopped session must not stay silently selected and
  // then be acted on by a bulk control.
  seed({ sessions: [session("s1", "a", "env")], selected: ["s1", "s-vanished"] });
  assert.deepEqual(selectedSessionIds(), ["s1"]);
});

test("sessionGroupCollapsed reads localStorage and survives junk in it", () => {
  const had = "localStorage" in globalThis;
  let store = "[]";
  globalThis.localStorage = { getItem: () => store, setItem: (_k, v) => { store = v; } };
  try {
    store = JSON.stringify(["env-a"]);
    assert.equal(sessionGroupCollapsed("env-a"), true);
    assert.equal(sessionGroupCollapsed("env-b"), false);

    // Corrupt storage must read as "nothing collapsed" rather than throw during a render.
    store = "{not json";
    assert.equal(sessionGroupCollapsed("env-a"), false);
    store = "null";
    assert.equal(sessionGroupCollapsed("env-a"), false);
  } finally {
    if (!had) delete globalThis.localStorage;
  }
});

test("SESSION_FILTER_KINDS is the shared agent-status vocabulary, not a private copy", async () => {
  // It is `AGENT_STATUSES` re-exported under the rail's name. A divergent copy would show filter chips for
  // statuses the resolver can never produce.
  const { AGENT_STATUSES } = await import("./status.js");
  assert.equal(SESSION_FILTER_KINDS, AGENT_STATUSES);
});

// ---------------------------------------------------------------------------------------------------
// Which session is CURRENT — the write side of the selection this module already reads.

import { ensureSelectedSession, selectedSession } from "./session-rail.mjs";

function seedSelection({ sessions = [], selectedId = "", selectedIds = [] } = {}) {
  state.sessions = sessions;
  state.selectedSessionId = selectedId;
  state.selectedSessionIds = new Set(selectedIds);
  state.selectedConversation = "";
}

test("with no sessions at all, the selection is cleared rather than left dangling", () => {
  // A selection pointing at nothing sends the next action into the void.
  seedSelection({ sessions: [], selectedId: "gone", selectedIds: ["gone", "also-gone"] });
  assert.equal(ensureSelectedSession(), null);
  assert.equal(state.selectedSessionId, "");
  assert.equal(state.selectedConversation, "dashboard", "the chat falls back to the dashboard thread");
  assert.equal(state.selectedSessionIds.size, 0, "the multi-select must be cleared too");
});

test("an existing selection is KEPT across a refresh", () => {
  // The poll runs every ~15s. Re-picking the first session each time would yank the operator's view away.
  seedSelection({
    sessions: [session("s1", "coder"), session("s2", "tester")],
    selectedId: "s2",
  });
  assert.equal(ensureSelectedSession().id, "s2");
  assert.equal(state.selectedSessionId, "s2");
  assert.equal(state.selectedConversation, "tester", "the chat follows the selected session's agent");
});

test("a selection whose session has disappeared falls back to the first", () => {
  seedSelection({ sessions: [session("s1", "coder")], selectedId: "vanished" });
  assert.equal(ensureSelectedSession().id, "s1");
  assert.equal(state.selectedSessionId, "s1");
});

test("stale multi-select ids are PRUNED, and live ones survive", () => {
  // Without this a bulk Stop/Delete fires against rows that are no longer on screen.
  seedSelection({
    sessions: [session("s1", "coder"), session("s2", "tester")],
    selectedId: "s1",
    selectedIds: ["s1", "s2", "s-vanished"],
  });
  ensureSelectedSession();
  assert.deepEqual([...state.selectedSessionIds].sort(), ["s1", "s2"]);
});

test("a session with no agent still selects, and the chat falls back to dashboard", () => {
  seedSelection({ sessions: [{ id: "s1" }], selectedId: "" });
  assert.equal(ensureSelectedSession().id, "s1");
  assert.equal(state.selectedConversation, "dashboard");
});

test("selectedSession returns null rather than undefined when nothing matches", () => {
  // Callers branch on it and then read fields off the result.
  seedSelection({ sessions: [session("s1", "coder")], selectedId: "other" });
  assert.equal(selectedSession(), null);
});
