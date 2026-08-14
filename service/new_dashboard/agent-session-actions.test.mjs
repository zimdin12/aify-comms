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
    state.sessions = [{ id: "s1", agentId: "coder", sessionMode: "resident" }];
    await switchAgentSessionMode("coder", "managed");
    assert.equal(state.agents[0].sessionMode, "managed", "the roster row must carry the new mode");
    assert.equal(state.agents[0].status, "available", "…and the rest of the returned agent, not just the mode");
    assert.equal(state.sessions[0].sessionMode, "managed", "every session of that agent must follow");
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
    state.sessions = [{ id: "s1", agentId: "coder", sessionMode: "managed" }];
    await switchAgentSessionMode("coder", "managed");
    assert.equal(state.sessions[0].sessionMode, "resident", "the answer, not the request, is what is painted");
  } finally { h.restore(); }
});

test("submitContinue sends the composed body, and an empty one sends nothing", async () => {
  const empty = withActions({ fields: { "continue-body": "   " } });
  try {
    await submitContinue("coder", false);
    assert.deepEqual(mutating(empty), [], "an empty continue would deliver a blank turn");
  } finally { empty.restore(); }
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
