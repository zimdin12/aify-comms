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
import { AGENT_PROCESSES_ID } from "./agent-processes.mjs";

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
  seed({ sessions: [{ id: "s1", agentId: "coder" }] });
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
  seed({ agents: [{ id: "coder", status: "online" }], sessions: [{ id: "s1", agentId: "coder" }], inspector: {} });
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
  seed({ agents: [{ id: "coder", status: "online" }], sessions: [{ id: "s1", agentId: "coder", sessionHandle: "h1" }], inspector: {} });
  withDom(drawerEls(), (els) => {
    openAgentDrawer("coder");
    assert.ok(!els["inspector-content"].innerHTML.includes("aify-comms.cmd"));
  });
});

// ---- Environment ------------------------------------------------------------------------------

/** The `<dd>` of the Environment row, or null when the row is absent. */
function environmentCell(html) {
  const match = /<dt>Environment<\/dt><dd>([^<]*)<\/dd>/.exec(html);
  return match ? match[1] : null;
}

test("THE ENVIRONMENT ROW shows a dash for a session with no binding", () => {
  // The row is written `sessionEnvironmentId(session) || '—'`, and that dash was unreachable:
  // the reader answered the display sentinel 'unassigned', which is truthy, so the drawer printed
  // the word. An operator reading it saw a value where there was none.
  seed({
    agents: [{ id: "coder" }],
    sessions: [{ id: "s1", agentId: "coder" }],
  });
  withDom(drawerEls(), (els) => {
    openAgentDrawer("coder");
    assert.equal(environmentCell(els["inspector-content"].innerHTML), "\u2014");
  });
});

test("…and the environment's LABEL when it has one", () => {
  // The other direction, so the dash cannot be produced by the row simply being broken.
  seed({
    agents: [{ id: "coder" }],
    sessions: [{ id: "s1", agentId: "coder", environmentId: "env-1" }],
    environments: [{ id: "env-1", label: "Windows on host" }],
  });
  withDom(drawerEls(), (els) => {
    openAgentDrawer("coder");
    assert.equal(environmentCell(els["inspector-content"].innerHTML), "Windows on host");
  });
});

// ---- Last seen -------------------------------------------------------------------------------
//
// WHAT THIS IS AND IS NOT. The age was ALREADY in the drawer, inside the status chip's `title` and
// `data-status-why` -- `statusWhyContext('agent', ...)` builds "Agent x is available. Last seen 42m
// ago." So this is not "the drawer never said"; it is "the drawer said it only on hover, over a chip
// reading `available`, which gives nobody a reason to hover". The change promotes it to a visible
// row beside the machine.
//
// MEASURED on the operator's fleet 2026-08-29: 18 of 47 agents silent for more than 30 days, three
// for 120, and TWO of those reading `available` -- a status that is honest, since the environment
// can cold-start them, and identical to the one an agent that answered forty seconds ago carries.
//
// EVERY ASSERTION BELOW READS THE ROW, not the drawer. The first version matched `/42m ago/` against
// the whole HTML and THREE of these four passed with the row deleted, because they were matching the
// tooltip. The mutation is what said so: removing the row reddened exactly one of the four.

/**
 * The `<dd>` of the Last seen row as PLAIN TEXT, or null when the row is absent.
 *
 * The age is now emitted by `relTimeHtml` as a `<span data-rel-ts>` so `rel-time-ticker.mjs` can keep
 * it true without repainting the drawer. That is markup INSIDE the cell, so the tag strip is what
 * lets these assertions keep reading the row rather than the tooltip -- the property the note above
 * says the first version of them got wrong.
 */
function lastSeenCell(html) {
  const match = /<dt>Last seen<\/dt><dd>(.*?)<\/dd>/.exec(html);
  return match ? match[1].replace(/<[^>]*>/g, "") : null;
}

/** The `data-rel-ts` the Last seen row carries, or null. The ticker updates nothing without it. */
function lastSeenStamp(html) {
  const match = /<dt>Last seen<\/dt><dd>.*?data-rel-ts="(\d+)".*?<\/dd>/.exec(html);
  return match ? Number(match[1]) : null;
}

test("the last-seen age carries its own instant, so the ticker can keep it true", () => {
  // WITHOUT THIS the age is a number frozen at render. `render-memo.mjs` will not repaint the drawer
  // until some FIELD moves, and "last seen 1m ago" beside an agent that died an hour ago is the
  // display asserting the opposite of the truth -- the reading that sent this session chasing a
  // live-looking timestamp on 2026-08-31.
  const at = Date.now() - 42 * 60 * 1000;
  seed({ agents: [{ id: "coder", lastSeen: new Date(at).toISOString() }] });
  withDom(drawerEls(), (els) => {
    openAgentDrawer("coder");
    const stamp = lastSeenStamp(els["inspector-content"].innerHTML);
    assert.ok(stamp, "the last-seen row carries no data-rel-ts, so nothing can refresh it");
    // To the second: the attribute must be the PARSED instant, not a re-encoded relative figure.
    assert.equal(Math.round(stamp / 1000), Math.round(at / 1000));
  });
});

test("an agent with NO lastSeen emits no timestamp to refresh", () => {
  // Anti-vacuity for the test above: a row that always carried a stamp would satisfy it while
  // claiming an age for an agent that never reported one.
  seed({ agents: [{ id: "coder" }] });
  withDom(drawerEls(), (els) => {
    openAgentDrawer("coder");
    const html = els["inspector-content"].innerHTML;
    // Prove the drawer actually rendered before reading a null out of it: without this the assertion
    // below passes against an empty string, which is the vacuous form of this test.
    assert.match(html, /Last seen/, "the drawer did not render, so the null below proves nothing");
    assert.equal(lastSeenStamp(html), null);
  });
});

test("the drawer says how long ago the agent was last seen", () => {
  const minutesAgo = new Date(Date.now() - 42 * 60 * 1000).toISOString();
  seed({ agents: [{ id: "coder", lastSeen: minutesAgo }] });
  withDom(drawerEls(), (els) => {
    openAgentDrawer("coder");
    assert.equal(lastSeenCell(els["inspector-content"].innerHTML), "42m ago");
  });
});

test("A LONG-DEAD AGENT READS DIFFERENTLY FROM A LIVE ONE", () => {
  // The whole point. `gov-tui` was 42 days silent and `available`; every visible row in the drawer
  // was the same as for an agent that answered a minute ago.
  const longAgo = new Date(Date.now() - 120 * 24 * 60 * 60 * 1000).toISOString();
  seed({ agents: [{ id: "ef-tech-lead", status: "available", lastSeen: longAgo }] });
  withDom(drawerEls(), (els) => {
    openAgentDrawer("ef-tech-lead");
    assert.equal(lastSeenCell(els["inspector-content"].innerHTML), "120d ago");
  });
});

test("IT FAILS CLOSED when there is no timestamp", () => {
  // `relTime` returns '' for a missing or unparseable value. Rendering that raw would produce
  // "Last seen  ago", and parsing it as an epoch would claim an age measured from 1970 -- the two
  // failure modes `environments-panels.mjs` names for the same field on the environment card.
  for (const agent of [{ id: "quiet" }, { id: "quiet", lastSeen: "" },
                       { id: "quiet", lastSeen: "not-a-timestamp" }]) {
    seed({ agents: [agent] });
    withDom(drawerEls(), (els) => {
      openAgentDrawer("quiet");
      const cell = lastSeenCell(els["inspector-content"].innerHTML);
      assert.equal(cell, "\u2014", `expected an em dash, got ${JSON.stringify(cell)}`);
    });
  }
});

test("it reads the field the service ACTUALLY EMITS, and no snake_case alternate", () => {
  // The first version accepted `agent.last_seen` as well, "cheap tolerance", with a test asserting
  // it worked. `test_the_dashboard_reads_only_agent_fields_the_service_emits` went red: the service
  // emits `lastSeen` and nothing else, so the alternate was a branch that could never fire -- the
  // exact shape that gate was written to remove after three of them were found in one sweep.
  const minutesAgo = new Date(Date.now() - 7 * 60 * 1000).toISOString();
  seed({ agents: [{ id: "coder", last_seen: minutesAgo }] });
  withDom(drawerEls(), (els) => {
    openAgentDrawer("coder");
    assert.equal(lastSeenCell(els["inspector-content"].innerHTML), "—",
      "a snake_case alternate is being read again; the service does not send one");
  });

  seed({ agents: [{ id: "coder", lastSeen: minutesAgo }] });
  withDom(drawerEls(), (els) => {
    openAgentDrawer("coder");
    assert.equal(lastSeenCell(els["inspector-content"].innerHTML), "7m ago");
  });
});

test("THE DRAWER LEAVES A CONTAINER FOR THE PROCESSES PANEL, and says it is loading", () => {
  // B5's CALL SITE, which the render tests in agent-processes.test.mjs cannot reach. A panel proven
  // to render correctly and never mounted is the shape this project has shipped before: six green
  // tests over a pure builder whose feature could not fire. This asserts the drawer actually leaves
  // the node -- keyed on the SHARED id, so a rename on either side fails here rather than silently
  // producing a panel that never fills.
  seed({ agents: [{ id: "coder" }], inspector: {} });
  withDom(drawerEls(), (els) => {
    openAgentDrawer("coder");
    const html = els["inspector-content"].innerHTML;
    assert.ok(html.includes(`id="${AGENT_PROCESSES_ID}"`), "no container for the processes panel");
    assert.match(html, /Processes/, "the panel has no heading, so an empty one reads as a gap");
    // A PLACEHOLDER, not an empty div. The fetch is asynchronous, and a blank space between the CLI
    // block and the actions row reads as a broken feature for however long the read takes.
    assert.match(html, /Reading this agent's terminals/);
  });
});

test("AND IT ACTUALLY MAKES THE READ, not merely leaves a slot for it", async () => {
  // THE GAP A MUTATION FOUND IN MY OWN GUARD. The test above asserts the CONTAINER exists; deleting
  // `loadAgentProcesses(id, ...)` from the drawer left it, and every other test here, green. A slot
  // nothing fills is the same defect as a panel nothing mounts, one layer along.
  //
  // DRIVEN THROUGH THE REAL `api`, with `fetch` stubbed so nothing leaves this machine. Injecting a
  // fake loader would prove the drawer calls SOMETHING; stubbing the transport proves the whole
  // path -- container, url, response, render -- and a live GET against the operator's own service
  // from a unit test is not an acceptable way to learn that.
  const hadFetch = "fetch" in globalThis;
  const realFetch = globalThis.fetch;
  const asked = [];
  globalThis.fetch = async (url) => {
    asked.push(String(url));
    return {
      ok: true,
      status: 200,
      text: async () => JSON.stringify({
        terminals: [{ id: "term_probe", status: "running", processId: "9001", cols: 157, rows: 32 }],
      }),
    };
  };
  const els = drawerEls();
  els[AGENT_PROCESSES_ID] = { innerHTML: "" };
  try {
    seed({ agents: [{ id: "coder" }], inspector: {} });
    globalThis.document = { getElementById: (id) => els[id] || null };
    openAgentDrawer("coder");
    // The call is deliberately fire-and-forget, so yield until its chain settles.
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(asked.length, 1, `the drawer made ${asked.length} reads, expected 1`);
    assert.match(asked[0], /\/terminals\?agentId=coder&status=all$/, `asked: ${asked[0]}`);
    assert.match(els[AGENT_PROCESSES_ID].innerHTML, /term_probe/, "the panel was never filled");
    assert.match(els[AGENT_PROCESSES_ID].innerHTML, /9001/, "the pid never reached the panel");
  } finally {
    delete globalThis.document;
    if (hadFetch) globalThis.fetch = realFetch; else delete globalThis.fetch;
  }
});
