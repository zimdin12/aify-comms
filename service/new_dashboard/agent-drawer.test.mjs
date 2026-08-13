// Real tests for the agent drawer and the selection sync that keeps it pointed at the right agent.
//
// None of this was testable while it lived in app.js — that file is reachable only by source regex, which
// can prove a line was written and nothing about whether it behaves. `syncInspectorToSelection` is a small
// state machine with five distinct outcomes, and every one of them is a way the drawer can end up showing
// the wrong agent or refusing to close.
//
// SEALING. `document` does not exist in Node; a minimal fake is installed per test and removed afterwards,
// so nothing here can pass by accident on a host that provides one. `state` is a shared singleton, so every
// field these functions read is rebuilt per test.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import { openAgentDrawer, sessionForAgent, syncInspectorToSelection } from "./agent-drawer.mjs";

function fakeEl(classes = []) {
  const set = new Set(classes);
  return {
    innerHTML: "",
    classList: {
      add: (c) => set.add(c),
      remove: (c) => set.delete(c),
      contains: (c) => set.has(c),
      _set: set,
    },
  };
}

function withDom(els, run) {
  const had = "document" in globalThis;
  globalThis.document = { getElementById: (id) => els[id] || null };
  try {
    return run(els);
  } finally {
    if (!had) delete globalThis.document;
  }
}

const drawerEls = (inspectorClasses = []) => ({
  inspector: fakeEl(inspectorClasses),
  "inspector-content": fakeEl(),
});

function seed({ agents = [], sessions = [], environments = [], inspector = {}, chat = {} } = {}) {
  state.agents = agents;
  state.sessions = sessions;
  state.environments = environments;
  state.inspector = inspector;
  state.chat = chat;
}

test("sessionForAgent finds the agent's session, and returns NULL when there is none", () => {
  seed({ sessions: [{ id: "s1", agent_id: "coder" }] });
  assert.equal(sessionForAgent("coder").id, "s1");

  // null, not undefined: callers branch on `session ? … : …` and then read fields off it. A stray
  // undefined would read identically here and differently at a `?? ` further down.
  assert.equal(sessionForAgent("nobody"), null);
});

test("openAgentDrawer ignores an empty or blank agent id", () => {
  seed({ inspector: { kind: "" } });
  withDom(drawerEls(), (els) => {
    for (const bad of ["", "   ", null, undefined]) {
      openAgentDrawer(bad);
      assert.equal(els.inspector.classList.contains("open"), false, `"${bad}" must not open the drawer`);
    }
  });
});

test("openAgentDrawer opens on an agent that is not in state.agents", () => {
  // The fallback is `|| { id }`. An agent that has vanished from the last poll must still open a drawer
  // rather than throw mid-render and leave the panel half-written.
  seed({ agents: [], inspector: {} });
  withDom(drawerEls(), (els) => {
    openAgentDrawer("ghost");
    assert.equal(state.inspector.kind, "agent");
    assert.equal(state.inspector.agentId, "ghost");
    assert.equal(els.inspector.classList.contains("open"), true);
    assert.ok(els["inspector-content"].innerHTML.includes("ghost"));
  });
});

test("opening an agent drawer clears the run-inspector styling", () => {
  // The same panel serves runs and agents. Leaving `run-inspector-sheet` on would render the agent drawer
  // with the run sheet's layout.
  seed({ agents: [{ id: "coder" }], inspector: { kind: "run", runId: "r1" } });
  withDom(drawerEls(["run-inspector-sheet"]), (els) => {
    openAgentDrawer("coder");
    assert.equal(els.inspector.classList.contains("run-inspector-sheet"), false);
    assert.equal(state.inspector.runId, "", "the previous run id must not survive into an agent drawer");
  });
});

test("syncInspectorToSelection does nothing unless an AGENT drawer is open", () => {
  seed({ inspector: { kind: "agent", agentId: "coder" }, chat: { selected: "dm:other" } });
  withDom(drawerEls(), () => {
    syncInspectorToSelection();                       // inspector element lacks `open`
    assert.equal(state.inspector.agentId, "coder", "a closed inspector must be left alone");
  });

  seed({ inspector: { kind: "run", runId: "r1" }, chat: { selected: "dm:other" } });
  withDom(drawerEls(["open"]), () => {
    syncInspectorToSelection();
    assert.equal(state.inspector.kind, "run", "a RUN inspector must not be retargeted by a chat selection");
  });
});

test("selecting something that is not a DM closes the drawer and clears it", () => {
  for (const selection of ["", "channel:general", "run:r1", null]) {
    seed({ inspector: { kind: "agent", agentId: "coder" }, chat: { selected: selection } });
    withDom(drawerEls(["open"]), (els) => {
      syncInspectorToSelection();
      assert.equal(els.inspector.classList.contains("open"), false, `"${selection}" must close the drawer`);
      assert.equal(state.inspector.kind, "");
      assert.equal(state.inspector.agentId, "");
    });
  }
});

test("selecting the SAME agent is a no-op — it does not re-render the drawer", () => {
  seed({ agents: [{ id: "coder" }], inspector: { kind: "agent", agentId: "coder" }, chat: { selected: "dm:coder" } });
  withDom(drawerEls(["open"]), (els) => {
    els["inspector-content"].innerHTML = "UNTOUCHED";
    syncInspectorToSelection();
    assert.equal(els["inspector-content"].innerHTML, "UNTOUCHED",
      "re-rendering on every sync would fight the operator's scroll position in the drawer");
  });
});

test("selecting a DIFFERENT agent retargets the drawer to it", () => {
  seed({
    agents: [{ id: "coder" }, { id: "tester" }],
    inspector: { kind: "agent", agentId: "coder" },
    chat: { selected: "dm:tester" },
  });
  withDom(drawerEls(["open"]), (els) => {
    syncInspectorToSelection();
    assert.equal(state.inspector.agentId, "tester");
    assert.ok(els["inspector-content"].innerHTML.includes("tester"));
    assert.equal(els.inspector.classList.contains("open"), true, "it must stay open across the switch");
  });
});

test("a bare `dm:` prefix with no agent is treated as no selection, not as agent ''", () => {
  seed({ inspector: { kind: "agent", agentId: "coder" }, chat: { selected: "dm:" } });
  withDom(drawerEls(["open"]), () => {
    syncInspectorToSelection();
    assert.equal(state.inspector.agentId, "coder", "an empty agent id must not blank the drawer's target");
  });
});

// ---------------------------------------------------------------------------------------------------
// The drawer's rendered controls, tested by RENDERING them.
//
// These replace regex assertions in `app.test.mjs` that matched the markup inside `openAgentDrawer` — the
// stop-worker button's key, the status gate, and the Continue-in-CLI block. They moved with the function,
// and they were only ever proof that a string had been typed. Rendering the drawer and reading the output
// can fail on a gate that lets the wrong status through, which the regexes could not.

test("the AGENT-level stop is offered only when there is a live worker to stop", () => {
  // Every OTHER lifecycle control here is gated on `sid`, a resolvable session row — which left an agent
  // whose session could not be resolved with no way to be stopped at all (operator report, 2026-07-26).
  // This one is keyed on the AGENT id and hits the agent-level teardown.
  for (const [status, expected] of [["online", true], ["working", true], ["blocked", true],
                                    ["offline", false], ["stopped", false], ["available", false]]) {
    seed({ agents: [{ id: "coder", status }], inspector: {} });
    withDom(drawerEls(), (els) => {
      openAgentDrawer("coder");
      const html = els["inspector-content"].innerHTML;
      assert.equal(html.includes('data-agent-stop-worker="coder"'), expected,
        `status "${status}" must ${expected ? "offer" : "withhold"} the agent-level stop`);
    });
  }
});

test("the session-scoped stop keeps a distinct label, and only appears with a session", () => {
  // Two destructive buttons in one drawer: confusing them costs the operator a worker they meant to keep.
  seed({ agents: [{ id: "coder", status: "online" }], sessions: [{ id: "s1", agent_id: "coder" }], inspector: {} });
  withDom(drawerEls(), (els) => {
    openAgentDrawer("coder");
    const html = els["inspector-content"].innerHTML;
    assert.ok(html.includes(">Stop session<"), "the session control keeps its own label");
    assert.ok(html.includes(">Stop worker<"), "…distinct from the agent-level one");
  });

  seed({ agents: [{ id: "coder", status: "online" }], sessions: [], inspector: {} });
  withDom(drawerEls(), (els) => {
    openAgentDrawer("coder");
    assert.ok(!els["inspector-content"].innerHTML.includes(">Stop session<"),
      "with no resolvable session there is nothing session-scoped to stop");
  });
});

test("the Continue-in-CLI block renders even when there is no command to give", () => {
  // An absent block reads as a broken feature; the block explains WHY there is no command instead.
  seed({ agents: [{ id: "coder", status: "online" }], sessions: [], inspector: {} });
  withDom(drawerEls(), (els) => {
    openAgentDrawer("coder");
    assert.match(els["inspector-content"].innerHTML, /continue-cli|Continue in CLI/i,
      "the Continue-in-CLI block must render unconditionally");
  });
});

test("copyable commands stay shell-neutral", () => {
  // PowerShell resolves the shim itself; a hardcoded .cmd is wrong everywhere else.
  seed({ agents: [{ id: "coder", status: "online" }], sessions: [{ id: "s1", agent_id: "coder", sessionHandle: "h1" }], inspector: {} });
  withDom(drawerEls(), (els) => {
    openAgentDrawer("coder");
    assert.ok(!els["inspector-content"].innerHTML.includes("aify-comms.cmd"));
  });
});
