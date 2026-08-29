// The lifecycle actions, tested by CALLING them.
//
// These are the buttons that change what an agent IS or stop what it is doing. Two of them end running
// work (`stopAgentWorker`, `requestSessionControl('stop')`) and two are irreversible (`removeAgent`,
// `deleteSessionById`). None of it was reachable by a test while it lived in app.js.
//
// So the assertions are about REQUESTS, not rendering: a control either reached the server or it did
// not. The confirmations are behaviour — an operator who answers No must have nothing sent — and every
// destructive action is checked in both directions, because a confirmation that is asked and then
// ignored is worse than no confirmation at all.

import assert from "node:assert/strict";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { state } from "./state.mjs";
import {
  deleteSessionById,
  initAgentSessionActions,
  openAgentChat,
  removeAgent,
  requestBulkSessionControl,
  requestSessionControl,
  resolveAgentSession,
  stopAgentWorker,
  submitContinue,
  switchAgentSessionMode,
} from "./agent-session-actions.mjs";

function makeEl(extra = {}) {
  return {
    innerHTML: "", textContent: "", className: "", value: "", hidden: false, disabled: false,
    checked: false, offsetParent: {}, dataset: {}, style: {}, children: [], firstElementChild: null,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    querySelector: () => null, querySelectorAll: () => [],
    addEventListener() {}, removeEventListener() {}, appendChild() {}, setAttribute() {},
    remove() {}, focus() {}, scrollTo() {},
    ...extra,
  };
}

/** A dialog overlay that answers on a microtask — see run-inspector.test.mjs for why the timing matters. */
function makeDialog(answer, typed) {
  const listeners = new Map();
  const button = (key) => makeEl({ addEventListener: (ev, fn) => { if (ev === "click") listeners.set(key, fn); } });
  const confirmBtn = button("confirm");
  const cancelBtn = button("cancel");
  const input = makeEl({ value: typed ?? "" });
  const overlay = makeEl({
    querySelector: (sel) => ({ ".dialog-confirm": confirmBtn, ".dialog-cancel": cancelBtn, ".dialog-input": input }[sel] ?? null),
    querySelectorAll: () => [cancelBtn, confirmBtn],
  });
  overlay.__answer = () => {
    input.value = typed ?? "";
    const fn = listeners.get(answer ? "confirm" : "cancel");
    if (fn) fn();
  };
  return overlay;
}

function withActions({ confirm = true, prompt = "typed", fields = {}, deps = {} } = {}) {
  const els = new Map();
  const sent = [];
  const saved = { document: globalThis.document, fetch: globalThis.fetch, requestAnimationFrame: globalThis.requestAnimationFrame };
  globalThis.requestAnimationFrame = (fn) => { fn(); return 1; };
  globalThis.document = {
    getElementById: (id) => {
      if (!els.has(id)) els.set(id, makeEl(id in fields ? { value: fields[id], checked: fields[id] === true } : {}));
      return els.get(id);
    },
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => makeDialog(confirm, prompt),
    addEventListener() {},
    removeEventListener() {},
    body: {
      appendChild: (el) => { if (el && el.className === "dialog-overlay") queueMicrotask(() => el.__answer()); },
      classList: { add() {}, remove() {} },
      style: { setProperty() {} },
    },
    activeElement: null,
  };
  globalThis.fetch = async (url, options = {}) => {
    const method = (options.method || "GET").toUpperCase();
    if (method !== "GET") sent.push({ url: String(url), method, body: options.body });
    const payload = { ok: true, agents: [], sessions: [] };
    return { ok: true, status: 200, statusText: "OK", json: async () => payload, text: async () => JSON.stringify(payload) };
  };
  setApiBase("");
  const calls = { refresh: 0, refreshSoon: 0, closeInspector: 0, renderSessionWorkspace: 0, setPage: [], chatClosed: 0, read: [] };
  initAgentSessionActions({
    chatController: { close: () => { calls.chatClosed += 1; }, open: (sel) => { state.chat.selected = sel; }, render() {}, renderRail() {}, renderConversation() {} },
    closeInspector: () => { calls.closeInspector += 1; },
    inspect: () => {},
    markConversationRead: async (id) => { calls.read.push(id); },
    refresh: async () => { calls.refresh += 1; },
    refreshSoon: () => { calls.refreshSoon += 1; },
    renderSessionWorkspace: () => { calls.renderSessionWorkspace += 1; },
    setPage: (page) => { calls.setPage.push(page); },
    ...deps,
  });
  Object.assign(state, {
    agents: [{ id: "coder", sessionMode: "resident" }],
    sessions: [{ id: "s1", agentId: "coder" }, { id: "s2", agentId: "tester" }],
    selectedSessionIds: new Set(),
    chat: { ...(state.chat || {}), selected: null, identity: "dashboard" },
  });
  return { els, sent, calls, restore: () => Object.assign(globalThis, saved) };
}

const mutating = (h) => h.sent.map((r) => `${r.method} ${r.url}`);

// --- the destructive four, both directions ------------------------------------------------------

test("STOPPING A WORKER IS CONFIRMED, AND No SENDS NOTHING", async () => {
  const no = withActions({ confirm: false });
  try {
    await stopAgentWorker("coder");
    assert.deepEqual(mutating(no), [], "answering No must not end the agent's turn");
  } finally { no.restore(); }

  const yes = withActions({ confirm: true });
  try {
    await stopAgentWorker("coder");
    assert.deepEqual(mutating(yes), ["POST /agents/coder/stop-worker"]);
  } finally { yes.restore(); }
});

test("stop-worker AWAITS the refresh before re-rendering the drawer", async () => {
  // Rendering straight after the POST painted the drawer from the PRE-stop roster, so it showed the
  // old status and a live "Stop worker" button for a worker that was already gone.
  const order = [];
  const h = withActions({ deps: { refresh: async () => { order.push("refresh"); } } });
  try {
    globalThis.fetch = async (url, options = {}) => {
      if ((options.method || "GET") !== "GET") order.push("post");
      return { ok: true, status: 200, statusText: "OK", text: async () => "{}" };
    };
    await stopAgentWorker("coder");
    assert.deepEqual(order, ["post", "refresh"], "the fresh state must be pulled before the redraw");
  } finally { h.restore(); }
});

test("a FAILING refresh still leaves the drawer usable", async () => {
  // The poll is best-effort here; losing it must not swallow the stop that already succeeded.
  const h = withActions({ deps: { refresh: async () => { throw new Error("poll down"); } } });
  try {
    await assert.doesNotReject(() => stopAgentWorker("coder"));
    assert.deepEqual(mutating(h), ["POST /agents/coder/stop-worker"], "the stop itself must still have gone out");
  } finally { h.restore(); }
});

test("REMOVING AN AGENT IS CONFIRMED — it tombstones the identity", async () => {
  const no = withActions({ confirm: false });
  try {
    await removeAgent("coder");
    assert.deepEqual(mutating(no), []);
    assert.equal(no.calls.closeInspector, 0, "nothing may be torn down when the operator said No");
  } finally { no.restore(); }

  const yes = withActions({ confirm: true });
  try {
    await removeAgent("coder");
    assert.deepEqual(mutating(yes), ["DELETE /agents/coder"]);
    assert.equal(yes.calls.closeInspector, 1, "the drawer for a removed agent must close");
    assert.equal(yes.calls.refreshSoon, 1);
  } finally { yes.restore(); }
});

test("DELETING A SESSION IS CONFIRMED", async () => {
  const no = withActions({ confirm: false });
  try {
    await deleteSessionById("s1");
    assert.deepEqual(mutating(no), []);
  } finally { no.restore(); }

  const yes = withActions({ confirm: true });
  try {
    await deleteSessionById("s1");
    assert.deepEqual(mutating(yes), ["DELETE /sessions/s1"]);
  } finally { yes.restore(); }
});

test("a session control is confirmed by default and NAMES what it will do", async () => {
  // `recreate` discards the native session. The three labels exist so the confirmation says which of
  // the three it is; a generic "Are you sure?" on a reset is how an operator loses a context window.
  for (const [action, path] of [["stop", "s1"], ["restart", "s1"], ["recreate", "s1"]]) {
    const no = withActions({ confirm: false });
    try {
      await requestSessionControl(path, action);
      assert.deepEqual(mutating(no), [], `${action} must not be sent when declined`);
    } finally { no.restore(); }
  }
  const yes = withActions({ confirm: true });
  try {
    await requestSessionControl("s1", "recreate");
    assert.deepEqual(mutating(yes), ["POST /sessions/s1/control"]);
    assert.equal(JSON.parse(yes.sent[0].body).action, "recreate");
    assert.equal(yes.calls.refresh, 1);
  } finally { yes.restore(); }
});

test("confirmAction=false skips the prompt — the flag the BULK path relies on", async () => {
  // Bulk asks once for the whole selection. If the per-session call still prompted, the operator would
  // answer the same question N times, and answering No to the fifth would leave four already done.
  const h = withActions({ confirm: false });
  try {
    await requestSessionControl("s1", "stop", false, false);
    assert.deepEqual(mutating(h), ["POST /sessions/s1/control"], "an unconfirmed call must go straight out");
    assert.equal(h.calls.refresh, 0, "…and refreshAfter=false must not poll per session");
  } finally { h.restore(); }
});

test("BULK CONTROL ASKS ONCE AND ACTS ON EVERY SELECTED SESSION", async () => {
  const h = withActions({ confirm: true });
  try {
    state.selectedSessionIds = new Set(["s1", "s2"]);
    await requestBulkSessionControl("stop");
    assert.deepEqual(mutating(h).sort(), ["POST /sessions/s1/control", "POST /sessions/s2/control"]);
  } finally { h.restore(); }
});

test("bulk control with NOTHING selected sends nothing", async () => {
  const h = withActions({ confirm: true });
  try {
    state.selectedSessionIds = new Set();
    await requestBulkSessionControl("stop");
    assert.deepEqual(mutating(h), [], "an empty selection must be a no-op, not an all-sessions action");
  } finally { h.restore(); }
});

test("declining the bulk confirmation stops ALL of them, not just the first", async () => {
  const h = withActions({ confirm: false });
  try {
    state.selectedSessionIds = new Set(["s1", "s2"]);
    await requestBulkSessionControl("stop");
    assert.deepEqual(mutating(h), []);
  } finally { h.restore(); }
});

// --- guards -------------------------------------------------------------------------------------

test("every action is a no-op on a missing id, rather than hitting an undefined path", async () => {
  // `/agents/undefined/stop-worker` is a real request that a server may answer surprisingly.
  const h = withActions({ confirm: true });
  try {
    await stopAgentWorker("");
    await removeAgent(null);
    await deleteSessionById(undefined);
    await requestSessionControl("", "stop");
    await requestSessionControl("s1", "");
    assert.deepEqual(mutating(h), []);
  } finally { h.restore(); }
});

test("a failed request TELLS THE OPERATOR instead of rejecting into nothing", async () => {
  for (const run of [
    () => stopAgentWorker("coder"),
    () => removeAgent("coder"),
    () => deleteSessionById("s1"),
    () => requestSessionControl("s1", "stop"),
  ]) {
    const h = withActions({ confirm: true });
    try {
      globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
      await assert.doesNotReject(run, "a network failure must be caught");
    } finally { h.restore(); }
  }
});

// --- the rest -----------------------------------------------------------------------------------

test("resolveAgentSession posts confirm and keep to different endpoints", async () => {
  // Sticky identity: an agent in `session-changed` is resolved by confirming the NEW id or keeping the
  // pinned handle. Sending one where the other was meant silently pins the wrong session.
  for (const mode of ["confirm", "keep"]) {
    const h = withActions({ confirm: true });
    try {
      await resolveAgentSession("coder", mode);
      assert.equal(h.sent.length, 1, `${mode} must send exactly one request`);
      assert.match(h.sent[0].url, new RegExp(mode), `${mode} must not reach the other endpoint`);
    } finally { h.restore(); }
  }
});

test("openAgentChat selects the conversation and navigates to Chat", async () => {
  const h = withActions();
  try {
    openAgentChat("coder");
    assert.equal(state.chat.selected, "dm:coder");
    assert.deepEqual(h.calls.setPage, ["chat"]);
  } finally { h.restore(); }
});

test("openAgentChat with no id (or 'dashboard') navigates but opens NO conversation", async () => {
  // Not a no-op, and my first version of this asserted it was. Landing on Chat is right — the operator
  // clicked something that means "go to Chat" — but selecting `dm:` or `dm:dashboard` would open a
  // conversation with nobody, or with the dashboard itself.
  for (const id of ["", null, "dashboard"]) {
    const h = withActions();
    try {
      state.chat.selected = "dm:previous";
      openAgentChat(id);
      assert.deepEqual(h.calls.setPage, ["chat"], `${JSON.stringify(id)} must still navigate to Chat`);
      assert.equal(state.chat.selected, "dm:previous", "…without switching the open conversation");
    } finally { h.restore(); }
  }
});

test("switchAgentSessionMode PATCHes the requested mode", async () => {
  const h = withActions({ confirm: true });
  try {
    await switchAgentSessionMode("coder", "managed", { force: true });
    const patch = h.sent.find((r) => r.method === "PATCH");
    assert.ok(patch, "a mode switch must reach the server");
    assert.match(patch.url, /\/agents\/coder\/session-mode$/);
    assert.equal(JSON.parse(patch.body).mode, "managed");
  } finally { h.restore(); }
});

test("A SUCCESSFUL SWITCH APPLIES THE SERVER'S ANSWER IMMEDIATELY, not on the next poll", async () => {
  // The poll is ~15s away. Without this the chip the operator just clicked keeps showing the OLD mode
  // for the rest of that window, which reads as the click not having worked — and the usual response to
  // that is to click it again.
  const h = withActions({ confirm: true });
  try {
    // `switchAgentSessionMode` calls fetch DIRECTLY and reads `res.json()` — it does not go through
    // `api()`, which reads `.text()`. A fake that only offers `text` makes the body silently null.
    const answer = { mode: "managed", agent: { id: "coder", sessionMode: "managed", status: "available" } };
    globalThis.fetch = async () => ({
      ok: true, status: 200, statusText: "OK",
      json: async () => answer, text: async () => JSON.stringify(answer),
    });
    state.sessions = [{ id: "s1", agentId: "coder", mode: "managed-warm" }];
    await switchAgentSessionMode("coder", "managed");
    assert.equal(state.agents[0].sessionMode, "managed", "the roster row must carry the new mode");
    assert.equal(state.agents[0].status, "available", "…and the rest of the returned agent, not just the mode");
    // THE AGENT ROW IS THE OPTIMISTIC PAINT. This used to assert `state.sessions[0].sessionMode`,
    // and a loop wrote that key so the assertion could find it -- but nothing reads it: the drawer
    // and the directory read `agent.sessionMode || session.mode`, the console reads
    // `agent?.sessionMode || session?.ownerMode`, and /sessions emits no `sessionMode` at all. The
    // write was inert and the test proved the write rather than the effect.
    assert.equal(state.sessions[0].sessionMode, undefined,
      "a key no reader consults must not be invented on the session row");
    assert.equal(h.calls.renderSessionWorkspace, 1, "…and the surface must repaint without waiting");
  } finally { h.restore(); }
});

test("the server's mode WINS over the requested one", async () => {
  // The API is allowed to answer with something other than what was asked. Trusting the request would
  // paint a mode the agent is not in.
  const h = withActions({ confirm: true });
  try {
    globalThis.fetch = async () => ({
      ok: true, status: 200, statusText: "OK",
      json: async () => ({ mode: "resident" }), text: async () => JSON.stringify({ mode: "resident" }),
    });
    state.agents = [{ id: "coder", sessionMode: "managed" }];
    await switchAgentSessionMode("coder", "managed");
    assert.equal(state.agents[0].sessionMode, "resident", "the answer, not the request, is what is painted");
  } finally { h.restore(); }
});

test("submitContinue sends nothing for a session id that does not exist", async () => {
  // WHAT THIS USED TO CLAIM, and why it proved nothing. It was written as "an empty continue sends
  // nothing", passed `"coder"` -- an AGENT id, where the function takes a SESSION id -- and set a
  // field named `continue-body`, which `submitContinue` does not read (it reads `cont-packet`). So it
  // returned at `Session not found` on its first line and never reached the behaviour in its name.
  // Kept, renamed to what it actually exercises, with a real case added beside it below.
  const missing = withActions();
  try {
    await submitContinue("coder", false);
    assert.deepEqual(mutating(missing), [], "an unknown session must not reach the server");
  } finally { missing.restore(); }
});

test("submitContinue posts the session's OWN environment and runtime", async () => {
  const h = withActions();
  try {
    state.sessions = [{ id: "s1", agentId: "coder", environmentId: "env-real", runtime: "claude-code" }];
    await submitContinue("s1", false);
    assert.deepEqual(mutating(h), ["POST /spawn-requests"]);
    const body = JSON.parse(h.sent[0].body);
    assert.equal(body.environmentId, "env-real");
    assert.equal(body.runtime, "claude-code");
  } finally { h.restore(); }
});

test("A SESSION WITH NO BINDING SENDS NOTHING, rather than posting the word on the screen", async () => {
  // THE DEFECT. `sessionEnvironmentId` used to answer the display sentinel 'unassigned' and
  // `sessionRuntime` answered 'runtime', so this exact call posted
  // `{environmentId: "unassigned", runtime: "runtime"}` to /spawn-requests -- which replies
  // `Environment "unassigned" not found`, naming an environment that has never existed on any host
  // and sending the operator to look for it. The two fields the form could not fill are now refused
  // by the form, which is the only place that knows they were never typed.
  const h = withActions();
  try {
    state.sessions = [{ id: "s1", agentId: "coder" }];
    await submitContinue("s1", false);
    assert.deepEqual(mutating(h), [], "a fabricated environment id must not reach /spawn-requests");
  } finally { h.restore(); }
});

test("EACH GUARD IS TESTED ALONE, so neither can be deleted quietly", async () => {
  // Both fields empty proves only that SOMETHING refused. Removing the environment guard left the
  // runtime guard blocking the same case, so the suite stayed green with half the fix gone. One
  // field at a time is what makes each guard's absence visible.
  for (const [label, session] of [
    ["no environment", { id: "s1", agentId: "coder", runtime: "claude-code" }],
    ["no runtime", { id: "s1", agentId: "coder", environmentId: "env-real" }],
  ]) {
    const h = withActions();
    try {
      state.sessions = [session];
      await submitContinue("s1", false);
      assert.deepEqual(mutating(h), [], `${label}: the request must not go out`);
    } finally { h.restore(); }
  }
});

test("a typed environment and runtime are enough on their own", async () => {
  // The other direction: the guard refuses a MISSING value, never a session that simply carries none
  // while the operator supplies it. A guard that cannot be satisfied is a broken button.
  const h = withActions({ fields: { "cont-env": "env-typed", "cont-runtime": "codex" } });
  try {
    state.sessions = [{ id: "s1", agentId: "coder" }];
    await submitContinue("s1", false);
    assert.deepEqual(mutating(h), ["POST /spawn-requests"]);
    const body = JSON.parse(h.sent[0].body);
    assert.equal(body.environmentId, "env-typed");
    assert.equal(body.runtime, "codex");
  } finally { h.restore(); }
});

test("INIT REFUSES A PARTIAL BAG", () => {
  const full = {
    chatController: { close() {}, open() {} }, closeInspector() {}, inspect() {}, markConversationRead: async () => {},
    refresh: async () => {}, refreshSoon() {}, renderSessionWorkspace() {}, setPage() {},
  };
  for (const missing of Object.keys(full)) {
    const partial = { ...full };
    delete partial[missing];
    assert.throws(() => initAgentSessionActions(partial), new RegExp(missing), `omitting ${missing} must throw`);
  }
  assert.doesNotThrow(() => initAgentSessionActions(full));
});
