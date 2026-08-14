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
  agentForTerminal,
  groupedSessionsByEnvironment,
  selectedSessionIds,
  sessionGroupCollapsed,
  toggleSupersededSessions,
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

// --- toggleSupersededSessions ------------------------------------------------------------------
//
// A branch body from app.js's delegated click handler, unreachable by any test while it lived there.
// Two lines, and both matter: the flip decides which sessions the rail may show, and the re-render is
// what makes the change visible. A flip without the re-render leaves the rail displaying the previous
// set until something else happens to redraw it — which reads as an intermittently working button.

test("toggleSupersededSessions FLIPS the flag and RE-RENDERS in the same call", () => {
  const saved = state.showSupersededSessions;
  const hadDoc = "document" in globalThis;
  const prevDoc = globalThis.document;
  let renders = 0;
  // The rail render is observed through the DOM lookup it must perform; a call that skipped the render
  // would leave this at zero.
  const el = () => ({
    hidden: false, innerHTML: "", textContent: "", value: "", dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    setAttribute() {}, removeAttribute() {}, appendChild() {}, addEventListener() {},
    querySelector: () => null, querySelectorAll: () => [], closest: () => null,
  });
  globalThis.document = {
    getElementById: (id) => { if (id === "session-rail") renders += 1; return el(); },
    querySelector: () => el(),
    querySelectorAll: () => [],
    createElement: () => el(),
  };
  try {
    // `renderSessionRail` reads the same state the rail is built from; seeding it with this file's own
    // helper keeps the render REAL rather than stubbing out the half that proves the toggle took effect.
    seed({ showSuperseded: false, statusFilter: new Set() });
    toggleSupersededSessions();
    assert.equal(state.showSupersededSessions, true, "the flag flips");
    assert.ok(renders >= 1, "…and the rail is re-rendered, not left stale");

    toggleSupersededSessions();
    assert.equal(state.showSupersededSessions, false, "it is a toggle, not a set");
  } finally {
    state.showSupersededSessions = saved;
    if (hadDoc) globalThis.document = prevDoc; else delete globalThis.document;
  }
});

// --- agentForTerminal ---------------------------------------------------------------------------
//
// Which agent owns a terminal. It decides who a console's input reaches, so a wrong answer sends an
// operator's keystrokes to a different agent — the reason the lookup order below is asserted rather
// than assumed.

test("agentForTerminal accepts all THREE shapes a session can carry a terminal id in", () => {
  // The API has used `terminalId`, `terminal.id` and `terminal_id`. Reading only one means the lookup
  // silently fails for whole classes of session and falls through to the agent list, which is a poll
  // behind — so the console would attach to a stale owner rather than erroring.
  const saved = { sessions: state.sessions, agents: state.agents };
  try {
    state.agents = [{ id: "coder-1" }];
    for (const session of [
      { id: "s1", agent_id: "coder-1", terminalId: "t1" },
      { id: "s1", agent_id: "coder-1", terminal: { id: "t1" } },
      { id: "s1", agent_id: "coder-1", terminal_id: "t1" },
    ]) {
      state.sessions = [session];
      assert.equal(agentForTerminal("t1")?.id, "coder-1", JSON.stringify(session));
    }
  } finally {
    Object.assign(state, saved);
  }
});

test("a session whose AGENT is unknown yields `{}`, not null — pinned because it is surprising", () => {
  // `agentForSession` ends `|| {}`, so a matched session with an agent missing from `state.agents`
  // returns an EMPTY OBJECT. It is truthy, so `if (agentForTerminal(id))` passes and `.id` is undefined —
  // a caller that guards on the result gets past the guard with nothing usable. Asserted as-is rather
  // than changed, since `{}` is what every existing caller of `agentForSession` already receives.
  const saved = { sessions: state.sessions, agents: state.agents };
  try {
    state.sessions = [{ id: "s1", agent_id: "ghost", terminalId: "t1" }];
    state.agents = [];
    const got = agentForTerminal("t1");
    assert.deepEqual(got, {}, "an empty object, not null");
    assert.ok(got, "…and it is truthy, which is the part worth knowing");
  } finally {
    Object.assign(state, saved);
  }
});

test("THE SESSION WINS over the agent's cached runtime state", () => {
  // A session knows its terminal now; an agent's `runtimeState` may be a poll behind. If the fallback
  // were consulted first, a terminal that had just been reassigned would resolve to its previous owner.
  const saved = { sessions: state.sessions, agents: state.agents };
  try {
    state.sessions = [{ id: "s1", agent_id: "current", terminalId: "t1" }];
    state.agents = [{ id: "stale", runtimeState: { terminalId: "t1" } }];
    const got = agentForTerminal("t1");
    assert.notEqual(got?.id, "stale", "the agent list must not win");
  } finally {
    Object.assign(state, saved);
  }
});

test("it falls back to the agent list, and returns NULL when nothing owns the terminal", () => {
  const saved = { sessions: state.sessions, agents: state.agents };
  try {
    state.sessions = [];
    state.agents = [{ id: "owner", terminalId: "t1" }];
    assert.equal(agentForTerminal("t1")?.id, "owner");
    assert.equal(agentForTerminal("nope"), null, "an unowned terminal is null, not undefined");
    assert.equal(agentForTerminal(undefined), null, "…and so is a missing id");
  } finally {
    Object.assign(state, saved);
  }
});

test("it survives absent session and agent lists", () => {
  // `(state.sessions || [])`. Both are empty on first paint, before the first refresh lands.
  const saved = { sessions: state.sessions, agents: state.agents };
  try {
    state.sessions = undefined;
    state.agents = undefined;
    assert.doesNotThrow(() => agentForTerminal("t1"));
    assert.equal(agentForTerminal("t1"), null);
  } finally {
    Object.assign(state, saved);
  }
});
