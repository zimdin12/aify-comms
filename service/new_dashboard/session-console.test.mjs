// The session console pane, tested by CALLING it.
//
// Extracted from app.js in v0.5.4 — 236 lines, unreachable by any test while it lived there. This is the
// surface an operator watches a managed agent through, and its job is a ROUTING decision: real PTY,
// synthesized RPC terminal, or plain transcript. Picking wrong does not error, it shows the operator the
// wrong thing — a dead transcript where a live console should be, or an xterm mounted against a
// terminal that does not exist.
//
// The three injected names all reach `refresh`, which is why they are injected: importing any of them
// would pull app.js's whole render web into this module.

import assert from "node:assert/strict";
import test from "node:test";

import { renderSessionConsole } from "./session-console.mjs";
import { state } from "./state.mjs";

/** A host element recording what was written into it. */
function host() {
  return {
    innerHTML: "",
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    querySelector: () => null,
    querySelectorAll: () => [],
    appendChild() {},
    setAttribute() {},
  };
}

function withDom(run) {
  const had = "document" in globalThis;
  const prev = globalThis.document;
  const saved = { agents: state.agents, sessions: state.sessions, activeXterm: state.activeXterm };
  globalThis.document = {
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => host(),
    body: { appendChild() {}, contains: () => true },
  };
  try {
    return run();
  } finally {
    Object.assign(state, saved);
    if (had) globalThis.document = prev; else delete globalThis.document;
  }
}

const deps = (over = {}) => ({
  mountXtermForTerminal: () => Promise.resolve(),
  refresh: () => {},
  resyncActiveConsole: async () => {},
  ...over,
});

test("a missing session is handled without throwing", async () => {
  // It is called from render paths that run on every poll, including before the first fetch lands.
  await withDom(async () => {
    assert.doesNotThrow(() => renderSessionConsole(null, host(), {}, deps()));
    assert.doesNotThrow(() => renderSessionConsole(undefined, host(), {}, deps()));
  });
});

test("a missing target element is handled without throwing", async () => {
  // The console pane is not in the DOM on every page. This runs anyway.
  await withDom(async () => {
    assert.doesNotThrow(() => renderSessionConsole({ id: "s1" }, null, {}, deps()));
  });
});

test("IT DOES NOT MOUNT AN XTERM FOR A SESSION WITH NO TERMINAL", async () => {
  // The routing decision that matters most. Mounting against a terminal id that does not exist leaves
  // an empty black pane the operator reads as a hung agent.
  await withDom(async () => {
    let mounts = 0;
    renderSessionConsole({ id: "s1", agent_id: "a1" }, host(), {}, deps({
      mountXtermForTerminal: () => { mounts += 1; return Promise.resolve(); },
    }));
    assert.equal(mounts, 0, "no terminal means no mount");
  });
});

test("omitting the dependency bag entirely does not throw", async () => {
  // `= {}` on the injected parameter. app.js always passes it, but the default is what stops a future
  // caller — or a test — from getting a destructuring TypeError instead of a render.
  await withDom(async () => {
    assert.doesNotThrow(() => renderSessionConsole({ id: "s1" }, host(), {}));
  });
});

test("A START REFUSAL CLEARS ONCE THE AGENT IS DEFINED: Sessions and Chat re-render it", async () => {
  // Review of 0.9.5, D7: both renders keyed on things the refusal is not, so a Start or Restart drawn
  // disabled for an undefined agent stayed disabled after its host defined it. A fresh host was right.
  const { mountChatConsole } = await import("./message-actions.mjs");
  await withDom(async () => {
    state.agents = [{ id: "a1", definitionState: "" }];
    state.sessions = [];
    const sessions = host();
    const stopped = { id: "s1", agent_id: "a1", agentId: "a1", status: "stopped" };
    renderSessionConsole(stopped, sessions, {}, deps());
    assert.match(sessions.innerHTML, /disabled title="no host defines a1/, "control: refused while undefined");
    const chat = host();
    mountChatConsole("a1", chat);
    assert.match(chat.innerHTML, /disabled title="no host defines a1/, "control: chat refused while undefined");

    state.agents = [{ id: "a1", definitionState: "defined" }];
    renderSessionConsole(stopped, sessions, {}, deps());
    mountChatConsole("a1", chat);
    assert.doesNotMatch(sessions.innerHTML, /no host defines/, "the Sessions Restart stayed refused after the agent was defined");
    assert.doesNotMatch(chat.innerHTML, /no host defines/, "the Chat Start stayed refused after the agent was defined");
  });
});
