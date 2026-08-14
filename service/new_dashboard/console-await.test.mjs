// The console "awaiting input" pill, tested by CALLING it.
//
// Both functions lived in app.js and were unreachable. The pill is what tells an operator that an agent
// is SITTING ON A PROMPT rather than working, and the two failure directions are both bad in ways nobody
// would attribute to this code: a false negative reads as an agent that silently stalled, and a false
// positive puts a "waiting" badge on an agent that is busy.
//
// THE TWO SIGNALS ARE NOT EQUIVALENT and the order matters. `blocked` is server-derived and
// authoritative — the status engine saw a real prompt pause the agent. The tail regex is a FALLBACK for
// consoles the engine does not classify, plain bash among them. Either one alone is insufficient, so
// both directions of the OR are asserted separately.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import { consoleAwaitingInputHint, updateAwaitPill } from "./console-await.mjs";

// --- consoleAwaitingInputHint ------------------------------------------------------------------

test("it recognises the prompt shapes agents actually emit", () => {
  // Each of these is a real prompt an operator would be stuck behind. Losing any one means that class of
  // stall shows no badge at all on an unclassified console.
  for (const tail of [
    "Do you want to continue? (y/n)",
    "Overwrite existing file? [y/N]",
    "Are you sure you want to delete this?",
    "Press Enter to continue",
    "proceed?",
    "Continue?",
    "Replace? (yes/no)",
  ]) {
    assert.equal(consoleAwaitingInputHint(tail), true, JSON.stringify(tail));
  }
});

test("MATCHING IS CASE-INSENSITIVE — the regex runs on a lowercased tail", () => {
  // Terminals emit whatever the tool prints. A case-sensitive test would miss "Are You Sure" and, more
  // realistically, "(Y/N)" — which is the commoner spelling in CLI prompts than the lowercase one.
  assert.equal(consoleAwaitingInputHint("ARE YOU SURE"), true);
  assert.equal(consoleAwaitingInputHint("Continue? (Y/N)"), true);
  assert.equal(consoleAwaitingInputHint("PRESS ENTER"), true);
});

test("ordinary output is NOT a prompt", () => {
  // The false-positive direction. A "waiting" badge on a working agent is worse than none, because it
  // invites an operator to interrupt something that is fine.
  for (const tail of [
    "Running tests...",
    "npm install completed in 4.2s",
    "yes, that worked",
    "continued from the previous step",
    "127 passing",
  ]) {
    assert.equal(consoleAwaitingInputHint(tail), false, JSON.stringify(tail));
  }
});

test("only the LAST 400 characters count", () => {
  // The pill is about what is on screen NOW. Scanning the whole buffer would leave it lit for the rest
  // of a session after a single prompt answered ten minutes ago.
  const stale = "continue? " + "x".repeat(500);
  assert.equal(consoleAwaitingInputHint(stale), false, "a prompt scrolled far above must not count");
  assert.equal(consoleAwaitingInputHint("x".repeat(500) + " continue?"), true, "…but a recent one does");
});

test("empty, blank and non-string input are all 'not waiting'", () => {
  // A console with no output yet is the normal state on attach, and it must not read as a prompt.
  for (const value of ["", "   ", "\n\t ", null, undefined, 0]) {
    assert.equal(consoleAwaitingInputHint(value), false, JSON.stringify(value));
  }
});

// --- updateAwaitPill ---------------------------------------------------------------------------

/** Install a pill element and the state it reads; returns the pill. */
function withPill({ agents = [], activeXterm = null, missing = false } = {}, run) {
  const savedAgents = state.agents;
  const savedXterm = state.activeXterm;
  const hadDoc = "document" in globalThis;
  const prevDoc = globalThis.document;
  const pill = { hidden: null };
  state.agents = agents;
  state.activeXterm = activeXterm;
  globalThis.document = {
    getElementById: (id) => (id === "console-await-pill" && !missing ? pill : null),
  };
  try {
    return run(pill);
  } finally {
    state.agents = savedAgents;
    state.activeXterm = savedXterm;
    if (hadDoc) globalThis.document = prevDoc; else delete globalThis.document;
  }
}

test("a BLOCKED agent shows the pill even when the console tail looks ordinary", () => {
  // The authoritative half. The status engine classified a real prompt; the tail may show nothing
  // recognisable, and the pill must still appear.
  withPill({
    agents: [{ id: "coder-1", status: "blocked" }],
    activeXterm: { agentId: "coder-1", recentText: "Running tests..." },
  }, (pill) => {
    updateAwaitPill();
    assert.equal(pill.hidden, false, "shown");
  });
});

test("any `blocked*` status counts — the check is a PREFIX", () => {
  // `startsWith('blocked')`. The engine emits qualified forms; an exact-equality test would miss them
  // and the pill would be dark for precisely the agents it exists for.
  for (const status of ["blocked", "blocked_on_input", "blocked-approval"]) {
    withPill({
      agents: [{ id: "a", status }],
      activeXterm: { agentId: "a", recentText: "" },
    }, (pill) => { updateAwaitPill(); assert.equal(pill.hidden, false, status); });
  }
});

test("an UNCLASSIFIED console shows the pill from the tail alone", () => {
  // The fallback half — a plain bash console the status engine never marks blocked. Without it these
  // stalls are invisible.
  withPill({
    agents: [{ id: "a", status: "working" }],
    activeXterm: { agentId: "a", recentText: "Overwrite? [y/N]" },
  }, (pill) => {
    updateAwaitPill();
    assert.equal(pill.hidden, false);
  });
});

test("a working agent with ordinary output HIDES the pill", () => {
  withPill({
    agents: [{ id: "a", status: "working" }],
    activeXterm: { agentId: "a", recentText: "compiling..." },
  }, (pill) => {
    updateAwaitPill();
    assert.equal(pill.hidden, true);
  });
});

test("no console open, or an unknown agent, hides rather than throws", () => {
  // `state.activeXterm?.agentId` and `?.recentText`. The pill updates on a poll that runs whether or not
  // a console is attached, so this is the common path, not an edge case.
  withPill({ agents: [], activeXterm: null }, (pill) => {
    assert.doesNotThrow(() => updateAwaitPill());
    assert.equal(pill.hidden, true);
  });
  withPill({ agents: [{ id: "other", status: "blocked" }], activeXterm: { agentId: "gone" } }, (pill) => {
    updateAwaitPill();
    assert.equal(pill.hidden, true, "another agent being blocked must not light this console's pill");
  });
});

test("a missing pill element is a silent no-op", () => {
  // `if (!pill) return;`. The console is one page of many and this runs on every refresh.
  withPill({ missing: true }, () => {
    assert.doesNotThrow(() => updateAwaitPill());
  });
});
