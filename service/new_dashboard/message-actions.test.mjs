// Message and channel actions, tested by CALLING them.
//
// The theme is OPTIMISM AND ITS REVERT. `toggleFavorite` flips the star before the server has agreed,
// because a star that waits for a round trip feels broken. That is the right call, and it makes the
// failure path the one that matters: if a rejected request does not put back exactly what was there,
// the dashboard silently disagrees with the server until the next poll and the operator acts on the
// difference — un-favouriting something that is still favourited, or the reverse.
//
// `markMessageRead` is the opposite arrangement — it waits, then applies — and that asymmetry is worth
// having pinned too, since the two live side by side and read as if they were the same shape.

import assert from "node:assert/strict";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { state } from "./state.mjs";
import {
  addChannelMember,
  chatChannelAction,
  initMessageActions,
  markConversationRead,
  markMessageRead,
  openMessageThread,
  removeChannelMember,
  toggleFavorite,
  unsendMessage,
} from "./message-actions.mjs";

function makeEl(extra = {}) {
  return {
    innerHTML: "", textContent: "", className: "", value: "", hidden: false, disabled: false,
    dataset: {}, style: {}, children: [], firstElementChild: null,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    querySelector: () => null, querySelectorAll: () => [], appendChild() {}, setAttribute() {},
    addEventListener() {}, removeEventListener() {}, remove() {}, focus() {}, scrollIntoView() {},
    ...extra,
  };
}

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

function withMessages({ confirm = true, prompt = "typed", failing = false } = {}) {
  const els = new Map();
  const sent = [];
  const saved = { document: globalThis.document, fetch: globalThis.fetch, requestAnimationFrame: globalThis.requestAnimationFrame };
  globalThis.requestAnimationFrame = (fn) => { fn(); return 1; };
  globalThis.document = {
    getElementById: (id) => { if (!els.has(id)) els.set(id, makeEl()); return els.get(id); },
    querySelector: () => null, querySelectorAll: () => [],
    createElement: () => makeDialog(confirm, prompt),
    addEventListener() {}, removeEventListener() {},
    body: {
      appendChild: (el) => { if (el && el.className === "dialog-overlay") queueMicrotask(() => el.__answer()); },
      classList: { add() {}, remove() {} }, style: { setProperty() {} },
    },
    activeElement: null,
  };
  globalThis.fetch = async (url, options = {}) => {
    const method = (options.method || "GET").toUpperCase();
    if (method !== "GET") sent.push({ url: String(url), method, body: options.body });
    if (failing) throw new TypeError("Failed to fetch");
    const payload = { ok: true, channels: [], messages: [] };
    return { ok: true, status: 200, statusText: "OK", json: async () => payload, text: async () => JSON.stringify(payload) };
  };
  setApiBase("");
  const calls = { rendered: 0, refreshSoon: 0, consoleRendered: 0 };
  initMessageActions({
    chatController: { render: () => { calls.rendered += 1; }, renderRail() {}, renderConversation() {} },
    refreshSoon: () => { calls.refreshSoon += 1; },
    renderSessionConsole: () => { calls.consoleRendered += 1; },
  });
  Object.assign(state, {
    agents: [{ id: "coder", favorited: false }],
    messages: [{ id: "m1", read: false }, { id: "m2", read: false }],
    sessions: [],
    chat: { ...(state.chat || {}), identity: "dashboard", selected: null, channels: [], peek: false },
  });
  return { els, sent, calls, restore: () => Object.assign(globalThis, saved) };
}

const mutating = (h) => h.sent.map((r) => `${r.method} ${r.url}`);

// --- optimism and revert ------------------------------------------------------------------------

test("FAVOURITING IS OPTIMISTIC — the star flips before the server answers", async () => {
  const h = withMessages();
  try {
    const done = toggleFavorite("coder");
    assert.equal(state.agents[0].favorited, true, "the flip must be visible immediately, not after the round trip");
    await done;
    assert.deepEqual(mutating(h), ["PATCH /agents/coder/favorite"]);
    assert.equal(JSON.parse(h.sent[0].body).favorited, true, "…and the server is told the SAME value");
  } finally { h.restore(); }
});

test("A FAILED FAVOURITE REVERTS TO EXACTLY WHAT WAS THERE", async () => {
  // Without the revert the star stays flipped until the next poll ~15s later, and the operator sees a
  // state the server never agreed to.
  const h = withMessages({ failing: true });
  try {
    await toggleFavorite("coder");
    assert.equal(state.agents[0].favorited, false, "a rejected favourite must be put back");
    assert.ok(h.calls.rendered >= 2, "…and the revert must be rendered, not just held in state");
  } finally { h.restore(); }
});

test("un-favouriting reverts the other way too", async () => {
  // Asserted separately because a revert written as `= false` rather than `= !next` passes the test
  // above and silently breaks this one.
  const h = withMessages({ failing: true });
  try {
    state.agents[0].favorited = true;
    await toggleFavorite("coder");
    assert.equal(state.agents[0].favorited, true, "a rejected un-favourite must go back to favourited");
  } finally { h.restore(); }
});

test("favouriting an UNKNOWN agent still posts and does not throw", async () => {
  const h = withMessages();
  try {
    await assert.doesNotReject(() => toggleFavorite("nobody"));
  } finally { h.restore(); }
});

test("MARK-READ WAITS FOR THE SERVER, unlike the favourite beside it", async () => {
  // The asymmetry, pinned. A read receipt applied optimistically and then failing would leave a message
  // showing as read that the server still considers unread — and unread is the state that drives the
  // dashboard's attention counts.
  const h = withMessages({ failing: true });
  try {
    await markMessageRead("m1", true);
    assert.equal(state.messages[0].read, false, "a failed read update must not have been applied");
  } finally { h.restore(); }

  const ok = withMessages();
  try {
    await markMessageRead("m1", true);
    assert.equal(state.messages[0].read, true);
    assert.deepEqual(mutating(ok), ["POST /messages/m1/read"]);
    assert.equal(JSON.parse(ok.sent[0].body).read, true);
  } finally { ok.restore(); }
});

test("mark-read sends the ACTING IDENTITY, not just the message", async () => {
  // Read state is per-identity. Omitting it would mark the message read for whoever the server
  // defaults to, which is not necessarily the operator's chosen identity.
  const h = withMessages();
  try {
    state.chat.identity = "manager-bot";
    await markMessageRead("m1", true);
    assert.equal(JSON.parse(h.sent[0].body).agentId, "manager-bot");
  } finally { h.restore(); }
});

test("mark-read can mark UNREAD as well as read", async () => {
  const h = withMessages();
  try {
    state.messages[0].read = true;
    await markMessageRead("m1", false);
    assert.equal(JSON.parse(h.sent[0].body).read, false);
    assert.equal(state.messages[0].read, false);
  } finally { h.restore(); }
});

// --- unsend -------------------------------------------------------------------------------------

test("UNSEND IS CONFIRMED — it removes the message for the recipient", async () => {
  const no = withMessages({ confirm: false });
  try {
    await unsendMessage("m1");
    assert.deepEqual(mutating(no), []);
    assert.equal(state.messages.length, 2, "declining must not remove it locally either");
  } finally { no.restore(); }

  const yes = withMessages({ confirm: true });
  try {
    await unsendMessage("m1");
    assert.deepEqual(mutating(yes), ["DELETE /messages/m1"]);
    assert.deepEqual(state.messages.map((m) => m.id), ["m2"], "only the unsent message goes");
  } finally { yes.restore(); }
});

test("a FAILED unsend leaves the message in place", async () => {
  // Removing it locally on failure would hide a message that is still delivered.
  const h = withMessages({ confirm: true, failing: true });
  try {
    await unsendMessage("m1");
    assert.deepEqual(state.messages.map((m) => m.id), ["m1", "m2"]);
  } finally { h.restore(); }
});

// --- channels -----------------------------------------------------------------------------------

test("channel actions are confirmed where they are destructive", async () => {
  const h = withMessages({ confirm: false });
  try {
    await chatChannelAction("delete", "general");
    assert.deepEqual(mutating(h), [], "declining a channel delete must send nothing");
  } finally { h.restore(); }
});

test("an unknown channel action sends nothing", async () => {
  const h = withMessages({ confirm: true });
  try {
    await chatChannelAction("not-an-action", "general");
    assert.deepEqual(mutating(h), []);
  } finally { h.restore(); }
});

test("adding a member reads the channel's OWN select, not a shared one", async () => {
  // The element id is per-channel (`chat-add-member-<name>`). A shared id would add whoever is
  // selected in a different channel's row — the click is on this channel but the value comes from
  // that one, and nothing about the result would look wrong.
  const h = withMessages();
  try {
    h.els.set("chat-add-member-general", makeEl({ value: "coder" }));
    h.els.set("chat-add-member-random", makeEl({ value: "tester" }));
    await addChannelMember("general");
    assert.equal(h.sent.length, 1);
    assert.match(h.sent[0].url, /channels\/general\/join/);
    assert.equal(JSON.parse(h.sent[0].body).agentId, "coder", "the value must come from general's select");
  } finally { h.restore(); }
});

test("adding a member with NOTHING SELECTED sends nothing and says why", async () => {
  const h = withMessages();
  try {
    h.els.set("chat-add-member-general", makeEl({ value: "" }));
    await addChannelMember("general");
    assert.deepEqual(mutating(h), [], "an empty selection must not post a blank member");
  } finally { h.restore(); }
});

test("removing a member is confirmed", async () => {
  const no = withMessages({ confirm: false });
  try {
    await removeChannelMember("general", "coder");
    assert.deepEqual(mutating(no), []);
  } finally { no.restore(); }

  const yes = withMessages({ confirm: true });
  try {
    await removeChannelMember("general", "coder");
    assert.equal(yes.sent.length, 1);
    assert.match(yes.sent[0].url, /general/);
  } finally { yes.restore(); }
});

// --- misc ---------------------------------------------------------------------------------------

test("MARKING A CONVERSATION READ TOUCHES ONLY MESSAGES ADDRESSED TO THE VIEWER", async () => {
  // `state.messages` is the FLEET-WIDE feed. Filtering by sender alone also swept up that agent's
  // messages to OTHER agents, which the server correctly 403s (the reader must be the recipient) —
  // so opening a chat spammed errors. The `to` half of the filter is the fix, and it is invisible in
  // the happy case: both filters mark the right message, only one of them also marks the wrong ones.
  const h = withMessages();
  try {
    state.chat.identity = "dashboard";
    state.messages = [
      { id: "mine", read: false, from: "coder", to: "dashboard" },
      { id: "not-mine", read: false, from: "coder", to: "tester" },
      { id: "already-read", read: true, from: "coder", to: "dashboard" },
    ];
    await markConversationRead("coder", { quiet: true });
    assert.equal(h.sent.length, 1, "exactly the one addressed to the viewer");
    assert.match(h.sent[0].url, /messages\/mine\/read/);
    assert.equal(state.messages[1].read, false, "another agent's message must stay unread");
  } finally { h.restore(); }
});

test("marking a conversation with NOTHING unread sends nothing", async () => {
  const h = withMessages();
  try {
    state.messages = [{ id: "m1", read: true, from: "coder", to: "dashboard" }];
    await markConversationRead("coder", { quiet: true });
    assert.deepEqual(mutating(h), []);
  } finally { h.restore(); }
});

test("openMessageThread with no id does nothing", async () => {
  const h = withMessages();
  try {
    assert.doesNotThrow(() => openMessageThread(""));
    assert.doesNotThrow(() => openMessageThread(null));
  } finally { h.restore(); }
});

test("INIT REFUSES A PARTIAL BAG", () => {
  const full = { chatController: { render() {} }, refreshSoon() {}, renderSessionConsole() {} };
  for (const missing of Object.keys(full)) {
    const partial = { ...full };
    delete partial[missing];
    assert.throws(() => initMessageActions(partial), new RegExp(missing), `omitting ${missing} must throw`);
  }
  assert.doesNotThrow(() => initMessageActions(full));
});
