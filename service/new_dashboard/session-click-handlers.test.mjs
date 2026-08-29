// The session workspace's filter and selection clicks, tested by CALLING them.
//
// All three were branch bodies inside app.js's delegated click handler, so nothing could reach them. They
// decide which sessions the operator can see and which ones a bulk action will act on — the second of
// which is the one worth being careful about, since a checkbox that mis-tracks its id sends an action to
// the wrong session.
//
// `renderSessionWorkspace` is the injected seam and doubles as the assertion: every one of these must
// re-render, because the filter state they mutate is only read during a render. A handler that updates the
// Set and never re-renders leaves the list showing the previous filter until something else redraws.

import assert from "node:assert/strict";
import test from "node:test";

import { SESSION_FILTER_KINDS } from "./session-rail.mjs";
import { state } from "./state.mjs";
import { LIVE_AGENT_STATUSES } from "./status.js";
import {
  applySessionStatusPreset,
  openAgentSessions,
  selectSessionRow,
  selectSessionTab,
  persistSessionStatusFilter,
  toggleSessionCheckbox,
  toggleSessionStatusFilter,
} from "./session-click-handlers.mjs";

/** Seal the shared `state` singleton and localStorage; return a render counter. */
function withSessions({ filter = [], selected = [], refuse = false } = {}, run) {
  const savedFilter = state.sessionStatusFilter;
  const savedIds = state.selectedSessionIds;
  const hadLs = "localStorage" in globalThis;
  const prev = globalThis.localStorage;
  const store = new Map();
  globalThis.localStorage = {
    setItem: (k, v) => { if (refuse) throw new Error("private mode"); store.set(k, v); },
    getItem: (k) => store.get(k) ?? null,
  };
  state.sessionStatusFilter = new Set(filter);
  state.selectedSessionIds = new Set(selected);
  let renders = 0;
  try {
    return run({ store, render: () => { renders += 1; }, renders: () => renders });
  } finally {
    state.sessionStatusFilter = savedFilter;
    state.selectedSessionIds = savedIds;
    if (hadLs) globalThis.localStorage = prev; else delete globalThis.localStorage;
  }
}

test("the 'all' preset selects every filter kind, and 'live' selects only the live ones", () => {
  // The two presets must differ. If both resolved to the same set, the chips would look independent and
  // behave identically — and `live` is the one an operator reaches for on a busy fleet.
  withSessions({}, (h) => {
    applySessionStatusPreset({ dataset: { sessionStatusPreset: "all" } }, h.render);
    assert.deepEqual([...state.sessionStatusFilter].sort(), [...SESSION_FILTER_KINDS].sort());

    applySessionStatusPreset({ dataset: { sessionStatusPreset: "live" } }, h.render);
    assert.deepEqual([...state.sessionStatusFilter].sort(), [...LIVE_AGENT_STATUSES].sort());
    assert.ok(LIVE_AGENT_STATUSES.length < SESSION_FILTER_KINDS.length, "live must be a strict subset");
  });
});

test("ANY OTHER preset value clears the filter entirely rather than leaving it stale", () => {
  // The ternary's final branch is `[]` — that is the "none" chip. Falling through to "leave it as it was"
  // would make the chip do nothing at all.
  withSessions({ filter: ["working"] }, (h) => {
    applySessionStatusPreset({ dataset: { sessionStatusPreset: "none" } }, h.render);
    assert.deepEqual([...state.sessionStatusFilter], []);
  });
});

test("every preset click PERSISTS and RE-RENDERS", () => {
  withSessions({}, (h) => {
    applySessionStatusPreset({ dataset: { sessionStatusPreset: "all" } }, h.render);
    assert.equal(h.renders(), 1, "the list must be redrawn");
    assert.ok(h.store.get("aifySessionStatusFilter"), "…and the choice survives a reload");
  });
});

test("toggleSessionStatusFilter ADDS then REMOVES the same kind", () => {
  // It is a toggle over a Set. Getting the membership test backwards would make the chips one-way.
  withSessions({}, (h) => {
    toggleSessionStatusFilter({ dataset: { sessionStatusFilter: "working" } }, h.render);
    assert.deepEqual([...state.sessionStatusFilter], ["working"]);

    toggleSessionStatusFilter({ dataset: { sessionStatusFilter: "working" } }, h.render);
    assert.deepEqual([...state.sessionStatusFilter], []);
    assert.equal(h.renders(), 2, "both directions re-render");
  });
});

test("toggling one kind leaves the others alone", () => {
  withSessions({ filter: ["working", "offline"] }, (h) => {
    toggleSessionStatusFilter({ dataset: { sessionStatusFilter: "offline" } }, h.render);
    assert.deepEqual([...state.sessionStatusFilter], ["working"]);
  });
});

test("A REFUSING STORAGE STILL FILTERS", () => {
  // `try { … } catch { /* ignore */ }` in persistSessionStatusFilter. The Set is mutated before the write
  // and the render happens after it, so an unguarded throw would leave the filter changed and the list
  // never redrawn — the chip would appear dead in private mode only.
  withSessions({ refuse: true }, (h) => {
    assert.doesNotThrow(() => toggleSessionStatusFilter({ dataset: { sessionStatusFilter: "working" } }, h.render));
    assert.deepEqual([...state.sessionStatusFilter], ["working"]);
    assert.equal(h.renders(), 1, "the render must still happen");
  });
});

test("persistSessionStatusFilter writes the Set as a JSON ARRAY", () => {
  // A Set does not survive JSON.stringify — it serialises as `{}`. The spread is what makes the filter
  // restorable at all, and the failure is silent: the write succeeds and restores nothing.
  withSessions({ filter: ["working", "offline"] }, (h) => {
    persistSessionStatusFilter();
    assert.deepEqual(JSON.parse(h.store.get("aifySessionStatusFilter")), ["working", "offline"]);
  });
});

test("toggleSessionCheckbox tracks the id from the ELEMENT'S checked state, not by flipping", () => {
  // The browser has already applied the check by the time the click handler runs, so the handler must
  // MIRROR `checked` rather than invert its own record. Flipping instead would desynchronise the moment
  // a re-render redraws the box, and a bulk action would then hit sessions the operator can see unticked.
  withSessions({}, (h) => {
    toggleSessionCheckbox({ checked: true, dataset: { sessionCheckbox: "s1" } }, h.render);
    assert.deepEqual([...state.selectedSessionIds], ["s1"]);

    // Same element, still checked: the selection must be idempotent, not toggled back off.
    toggleSessionCheckbox({ checked: true, dataset: { sessionCheckbox: "s1" } }, h.render);
    assert.deepEqual([...state.selectedSessionIds], ["s1"], "re-affirming a checked box keeps it");

    toggleSessionCheckbox({ checked: false, dataset: { sessionCheckbox: "s1" } }, h.render);
    assert.deepEqual([...state.selectedSessionIds], []);
  });
});

test("checkbox selection is per-id and always re-renders", () => {
  withSessions({ selected: ["s1"] }, (h) => {
    toggleSessionCheckbox({ checked: true, dataset: { sessionCheckbox: "s2" } }, h.render);
    assert.deepEqual([...state.selectedSessionIds].sort(), ["s1", "s2"]);
    assert.equal(h.renders(), 1);
  });
});

// --- row selection and the agent → sessions jump -------------------------------------------------

/** Seal the selection fields these two touch. */
function withSelection({ sessions = [], selectedId = "" } = {}, run) {
  const saved = {
    sessions: state.sessions,
    selectedSessionId: state.selectedSessionId,
    selectedConversation: state.selectedConversation,
  };
  state.sessions = sessions;
  state.selectedSessionId = selectedId;
  state.selectedConversation = "";
  try {
    return run();
  } finally {
    Object.assign(state, saved);
  }
}

test("selectSessionRow points the CONVERSATION at the selected session's agent", () => {
  // Two fields move together. Selecting the row without repointing the conversation leaves the chat
  // pane showing the previous session's agent — the operator then reads one session and messages
  // another, which is the failure worth guarding.
  withSelection({ sessions: [{ id: "s1", agentId: "coder-1" }] }, () => {
    let renders = 0;
    selectSessionRow({ dataset: { sessionSelect: "s1" } }, () => { renders += 1; });
    assert.equal(state.selectedSessionId, "s1");
    assert.equal(state.selectedConversation, "coder-1");
    assert.equal(renders, 1);
  });
});

test("a session with NO agent falls back to 'dashboard', never to empty", () => {
  // `sessionAgentId(session) || 'dashboard'`. An empty conversation key would address no thread at all,
  // so the chat pane would silently render nothing rather than the dashboard's own.
  withSelection({ sessions: [{ id: "s1" }] }, () => {
    selectSessionRow({ dataset: { sessionSelect: "s1" } }, () => {});
    assert.equal(state.selectedConversation, "dashboard");
  });
});

test("selecting an UNKNOWN session id still yields a usable conversation", () => {
  // `session ? … : 'dashboard'`. The rail can be one poll behind the click; a missing session must not
  // leave the conversation key undefined.
  withSelection({ sessions: [] }, () => {
    selectSessionRow({ dataset: { sessionSelect: "gone" } }, () => {});
    assert.equal(state.selectedSessionId, "gone");
    assert.equal(state.selectedConversation, "dashboard");
  });
});

test("openAgentSessions NAVIGATES and CLOSES THE INSPECTOR even with no session to select", () => {
  // `if (sid) { … }` guards only the selection. The page change and the inspector close are
  // unconditional on purpose: the button's job is to get the operator to the Sessions page, and an agent
  // with no live session is exactly when they most need to look.
  withSelection({}, () => {
    const pages = [];
    let closed = 0;
    let renders = 0;
    openAgentSessions(
      { dataset: {} },
      () => { renders += 1; },
      (p) => pages.push(p),
      () => { closed += 1; },
    );
    assert.deepEqual(pages, ["sessions"]);
    assert.equal(closed, 1, "the inspector must not stay open over the new page");
    assert.equal(renders, 0, "…but nothing is re-rendered when there is no session to select");
    assert.equal(state.selectedSessionId, "", "and no selection is invented");
  });
});

test("openAgentSessions selects the named session before navigating", () => {
  withSelection({}, () => {
    const pages = [];
    let renders = 0;
    openAgentSessions(
      { dataset: { agentOpenSessions: "s9" } },
      () => { renders += 1; },
      (p) => pages.push(p),
      () => {},
    );
    assert.equal(state.selectedSessionId, "s9");
    assert.equal(renders, 1, "the workspace is redrawn for the new selection");
    assert.deepEqual(pages, ["sessions"]);
  });
});

test("selectSessionTab DEFAULTS to console when the tab is missing or empty", () => {
  // `|| 'console'`. An undefined tab would be written into state and matched against no panel, so the
  // detail pane would render empty — which looks like a session with no data rather than a bad click.
  const saved = state.selectedSessionTab;
  try {
    for (const [raw, expected] of [["logs", "logs"], [undefined, "console"], ["", "console"]]) {
      let renders = 0;
      selectSessionTab({ dataset: { sessionTab: raw } }, () => { renders += 1; });
      assert.equal(state.selectedSessionTab, expected, JSON.stringify(raw));
      assert.equal(renders, 1, "every tab click re-renders");
    }
  } finally {
    state.selectedSessionTab = saved;
  }
});
